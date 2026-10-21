# -*- coding: utf-8 -*-
"""任务状态机 —— 异步 Job 的状态流转、断点落盘与增量计划。

《爬虫标准API接口.md》v1.2 §7 要求 Job 是"可暂停 / 可续跑 / 可增量"的异步任务；
现有 `api.crawl` 是一次性同步函数，没有状态、没有断点。本模块只做**任务账本**：

- 一个 job 一份 JSON（路径由 `Path_Store.JOBS_DIR` 登记，本模块不自己拼 data/jobs）；
- 状态机是显式的：`TRANSITIONS` 是唯一出处，非法流转抛 ValueError；
- `plan` 拿 pages.json 的 CrawlResult 列表做增量判断（fresh → skip，过期/失败 → 重抓）；
- `mark` 写单条结果并重算 counts / progress，`settle` 收口终态，`resume` 给出续跑清单。

================================ 结构树 =================================
① 纯逻辑（不碰文件）
    _parse_ts(value) -> datetime | None       时间统一成带时区 datetime
    _skip_reason(record, now, days) -> str|None
                                              增量判断：fresh / None（需要重抓）
    _settle_target(counts) -> str              完成情况 → done/partial/failed/running
    _count_items(job) -> (counts, progress)    计数的唯一实现
② 状态机与结果
    transition(job, to_status)                 显式校验后改状态（唯一改状态的入口）
    new_job(job_id, urls, ...)                 新 job 骨架（items 全部 pending）
    mark(job, url, record)                     写入一条 CrawlResult 结果
    progress(job) -> {"total","done","ok","failed","skipped","percent"}
    settle(job)                                按完成情况收口终态
    resume(job) -> {"todo": [...]}             还没做完（pending/queued）的 URL
    plan(job, known, ...)                      增量计划（todo / skip / mode）
③ 落盘（只经 io_utils）
    job_path(job_id, directory=None) -> str
    save_job(job, directory=None) -> str
    load_job(job_id, directory=None) -> dict | None
④ 自测
    _self_test()                               只用 tempfile，零网络

================================ 调用关系 ================================
api.py ──▶ new_job / plan / transition / mark / settle / resume ──▶ save_job
        └─ mark 的 record 来自 api._crawl_one（record.build_*，字段见 record.py）
plan 的 known 来自 store.load_index()（data/raw/pages.json 的 CrawlResult 列表）。

================================ 状态机 ================================
    queued ──▶ running ──▶ paused ──▶ running
       │          │            │
       ├──────────┴────────────┴──▶ done / partial / failed / cancelled（终态）
    partial / failed ──▶ running | queued（允许重跑）；done / cancelled 是终态
    "同状态" 视为幂等（不算流转，不抛错）。

================================ 对外返回契约 ================================
job 是纯 JSON dict，字段固定为：
    job_id / project_id / status / mode / created_at / started_at / finished_at /
    updated_at / request / urls / items / counts / progress
items[url] = {url, status, record_id, error_code, updated_at}
    status ∈ pending | queued | running | ok | ok_pdf | skipped | failed
progress(job) 恰好返回 total / done / ok / failed / skipped / percent 五个键；
    done = ok + failed + skipped；percent = done/total*100（保留 1 位小数，total=0 → 0.0）。
"""

from __future__ import annotations

import copy
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils, record
except ImportError:                                  # 直接运行本文件（自测入口）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import io_utils, record

# ---------------------------------------------------------------- 常量区
STATUSES = ("queued", "running", "paused", "done", "partial", "failed", "cancelled")
TERMINAL_STATUSES = ("done", "partial", "failed", "cancelled")
ITEM_TERMINAL_STATUSES = ("ok", "ok_pdf", "skipped", "failed")
ITEM_PENDING_STATUSES = ("queued", "pending")
PLAN_MODES = ("full", "incremental")

# 唯一出处：每个状态允许迁到哪些状态（同状态幂等，不写进表里）
TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "paused", "cancelled", "done", "partial", "failed"},
    "running": {"paused", "cancelled", "done", "partial", "failed"},
    "paused": {"running", "cancelled", "done", "partial", "failed"},
    "failed": {"running", "queued", "cancelled", "done", "partial"},
    "partial": {"running", "queued", "cancelled", "done", "failed"},
    "done": set(),                       # 终态：不再流转
    "cancelled": set(),                  # 终态：用户已取消，settle 也不覆盖
}

# CrawlResult.status → item.status（未知值按 failed 处理，宁可暴露也不假装成功）
_ITEM_STATUS_BY_RECORD = {"ok": "ok", "ok_pdf": "ok_pdf",
                          "skipped": "skipped", "failed": "failed"}


# ===== ① 纯逻辑 =====
def _now_iso() -> str:
    """统一时间戳来源：复用 record.now_iso（本地时区、秒级），不另造一套。"""
    return record.now_iso()


def _parse_ts(value: Any) -> datetime | None:
    """把 ISO8601 字符串 / datetime / 时间戳统一成带时区的 datetime；坏值返回 None。"""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, bool):
        return None
    elif isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()          # 无时区按本机时区解释（与 record.now_iso 一致）
    return moment


def _skip_reason(known_record: dict[str, Any] | None, now: datetime,
                 recheck_days: float) -> str | None:
    """增量判断：上次成功且够新、且有内容指纹 → 返回 "fresh"；否则 None（要重抓）。"""
    if not isinstance(known_record, dict):
        return None
    status = str(known_record.get("status") or "").lower()
    if status not in ("ok", "ok_pdf"):
        return None
    if not known_record.get("content_sha256"):
        return None
    fetched = _parse_ts(known_record.get("fetched_at"))
    if fetched is None:
        return None
    if now - fetched > timedelta(days=recheck_days):
        return None                            # 过了复核周期 → 重抓
    return "fresh"


def _settle_target(counts: dict[str, int]) -> str:
    """按完成情况给出收口状态；还有没做完的 → running。"""
    total = counts.get("total", 0)
    if total == 0:
        return "done"
    if counts.get("pending", 0) or counts.get("running", 0):
        return "running"
    if counts.get("ok", 0) == 0 and counts.get("failed", 0) == 0:
        return "done"                          # 全部 skipped：没失败，算跑完
    if counts.get("ok", 0) == 0:
        return "failed"
    if counts.get("failed", 0) > 0:
        return "partial"
    return "done"


def _count_items(job: dict[str, Any]) -> tuple[dict[str, int], dict[str, Any]]:
    """计数的唯一实现：counts（账本）与 progress（对外契约）一起算。"""
    items = job.get("items") or {}
    buckets = {"pending": 0, "running": 0, "ok": 0, "failed": 0, "skipped": 0}
    for item in items.values():
        status = item.get("status") if isinstance(item, dict) else None
        if status in ("ok", "ok_pdf"):
            buckets["ok"] += 1
        elif status == "failed":
            buckets["failed"] += 1
        elif status == "skipped":
            buckets["skipped"] += 1
        elif status == "running":
            buckets["running"] += 1
        else:                                  # queued / pending / 未知 → 未完成
            buckets["pending"] += 1
    total = len(items)
    done = buckets["ok"] + buckets["failed"] + buckets["skipped"]
    counts = {"total": total, "done": done, "pending": buckets["pending"],
              "running": buckets["running"], "ok": buckets["ok"],
              "failed": buckets["failed"], "skipped": buckets["skipped"]}
    percent = round(done * 100.0 / total, 1) if total else 0.0
    progress = {"total": total, "done": done, "ok": buckets["ok"],
                "failed": buckets["failed"], "skipped": buckets["skipped"],
                "percent": percent}
    return counts, progress


def _recount(job: dict[str, Any]) -> dict[str, Any]:
    """重算并写回 counts / progress，返回 progress。"""
    counts, progress = _count_items(job)
    job["counts"] = counts
    job["progress"] = progress
    return progress


def _job_urls(job: dict[str, Any]) -> list[str]:
    """job 的 URL 顺序：以 urls 为准，items 里多出来的（手工改过）追加在后面。"""
    order: list[str] = []
    for url in job.get("urls") or []:
        if isinstance(url, str) and url not in order:
            order.append(url)
    for url in (job.get("items") or {}):
        if url not in order:
            order.append(url)
    return order


def _require_job(job: Any) -> dict[str, Any]:
    """最轻量的结构校验：进函数先确认拿到的是一只 job。"""
    if not isinstance(job, dict):
        raise ValueError("job 必须是 dict")
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job 缺少合法的 job_id")
    if not isinstance(job.get("items"), dict):
        raise ValueError("job 缺少 items 字典")
    return job


# ===== ② 状态机与结果 =====
def _touch(job: dict[str, Any]) -> None:
    job["updated_at"] = _now_iso()


def transition(job: dict[str, Any], to_status: str) -> dict[str, Any]:
    """显式状态流转：终态不可离开、不许倒退、未知状态直接抛 ValueError。

    这是**唯一**改 job["status"] 的入口（new_job 之外）；同状态调用幂等。
    """
    _require_job(job)
    current = job.get("status")
    if current not in STATUSES:
        raise ValueError(f"job 当前状态非法：{current!r}")
    if to_status not in STATUSES:
        raise ValueError(f"未知状态：{to_status!r}（可选：{STATUSES}）")
    if to_status == current:
        return job
    if to_status not in TRANSITIONS[current]:
        allowed = sorted(TRANSITIONS[current]) or ["（无，终态）"]
        raise ValueError(f"非法状态流转：{current} → {to_status}；允许：{allowed}")
    job["status"] = to_status
    if to_status == "running":
        job["finished_at"] = None
        if not job.get("started_at"):
            job["started_at"] = _now_iso()
    if to_status in TERMINAL_STATUSES:
        job["finished_at"] = _now_iso()
    _touch(job)
    return job


def _clean_urls(urls: Iterable[str] | str | None) -> list[str]:
    """URL 清单：去空白、去重、保持首次出现的顺序；非字符串直接暴露。"""
    if urls is None:
        candidates: list[Any] = []
    elif isinstance(urls, str):
        candidates = [urls]
    else:
        candidates = list(urls)
    cleaned: list[str] = []
    for raw in candidates:
        if not isinstance(raw, str):
            raise ValueError(f"urls 里只能是字符串，收到：{type(raw).__name__}")
        text = raw.strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _norm_started(started_at: Any) -> str | None:
    if started_at is None:
        return None
    if isinstance(started_at, datetime):
        moment = started_at if started_at.tzinfo else started_at.astimezone()
        return moment.isoformat(timespec="seconds")
    text = str(started_at).strip()
    return text or None


def new_job(job_id: str, urls: Iterable[str] | str, *, project_id: str | None = None,
            request: dict[str, Any] | None = None,
            started_at: datetime | str | None = None) -> dict[str, Any]:
    """新建 job 骨架。

    - 传入 `started_at` 表示任务已经开始（status=running），否则 status=queued；
    - `urls` 去重保序，items 全部 pending；counts / progress 初始为 0；
    - `request` 深拷贝，避免调用方后续改动回灌到 job。
    """
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id 不能为空")
    if request is not None and not isinstance(request, dict):
        raise ValueError("request 必须是 dict 或 None")
    now = _now_iso()
    order = _clean_urls(urls)
    started = _norm_started(started_at)
    job: dict[str, Any] = {
        "job_id": job_id.strip(),
        "project_id": project_id,
        "status": "running" if started else "queued",
        "mode": None,
        "created_at": now,
        "started_at": started,
        "finished_at": None,
        "updated_at": now,
        "request": copy.deepcopy(request) if request else {},
        "urls": order,
        "items": {},
        "counts": {},
        "progress": {},
    }
    for url in order:
        job["items"][url] = {"url": url, "status": "pending", "record_id": None,
                             "error_code": None, "updated_at": now}
    _recount(job)
    return job


def add_urls(job: dict[str, Any], urls: Iterable[str] | str) -> dict[str, Any]:
    """往已有 job 里追加待抓 URL（链接发现用）：已存在的跳过，新增的记 pending。

    链接发现会在抓取过程中不断冒出新的详情页/附件，job 的 URL 集合必须能长大；
    追加后立刻重算 counts / progress，保证 `progress()` 任何时候都是真账。
    """
    _require_job(job)
    for url in _clean_urls(urls):
        if url in job["items"]:
            continue
        if url not in job["urls"]:
            job["urls"].append(url)
        job["items"][url] = {"url": url, "status": "pending", "record_id": None,
                             "error_code": None, "updated_at": _now_iso()}
    _recount(job)
    _touch(job)
    return job


def mark(job: dict[str, Any], url: str, record: dict[str, Any]) -> dict[str, Any]:
    """把一条 CrawlResult 记到 items[url] 上，并重算 counts / progress。

    写入 status / record_id / error_code（error.code）；未知 record.status 按 failed 收，
    宁可让失败可见，也不假装成功。URL 不在 job 里属于接线错误 → ValueError。
    """
    _require_job(job)
    items = job["items"]
    if url not in items:
        raise ValueError(f"URL 不在该 job 内：{url}")
    if not isinstance(record, dict):
        raise ValueError("record 必须是 CrawlResult dict")

    record_status = str(record.get("status") or "").lower()
    item_status = _ITEM_STATUS_BY_RECORD.get(record_status, "failed")
    error = record.get("error")
    error_code = error.get("code") if isinstance(error, dict) else None

    item = items[url]
    item["status"] = item_status
    item["record_id"] = record.get("record_id")
    item["error_code"] = error_code
    item["updated_at"] = _now_iso()
    _recount(job)
    _touch(job)
    return job


def progress(job: dict[str, Any]) -> dict[str, Any]:
    """对外进度契约：{total, done, ok, failed, skipped, percent}（只读，不改 job）。"""
    _require_job(job)
    return _count_items(job)[1]


def settle(job: dict[str, Any]) -> dict[str, Any]:
    """按完成情况收口：done / partial / failed / running。

    终态（done/partial/failed/cancelled）不再改；paused 且还有活 → 保持 paused，
    暂停是用户状态，不能被收口悄悄改回 running。
    """
    _require_job(job)
    _recount(job)
    current = job.get("status")
    if current in TERMINAL_STATUSES:
        return job
    target = _settle_target(job["counts"])
    if current == "paused" and target == "running":
        return job
    transition(job, target)
    return job


def resume(job: dict[str, Any]) -> dict[str, Any]:
    """续跑清单：只返回还没做完（pending / queued）的 URL，保持 job 内顺序。"""
    _require_job(job)
    todo: list[str] = []
    for url in _job_urls(job):
        item = job["items"].get(url) or {}
        if item.get("status") in ITEM_PENDING_STATUSES:
            todo.append(url)
    return {"todo": todo}


def _newer_than(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    """候选记录是否比已收录的更新（fetched_at 更晚）；解析不了的时间一律不替换。"""
    candidate_ts = _parse_ts(candidate.get("fetched_at"))
    if candidate_ts is None:
        return False
    existing_ts = _parse_ts(existing.get("fetched_at"))
    return existing_ts is None or candidate_ts > existing_ts


def _put_known(mapping: dict[str, dict[str, Any]], url: Any,
               value: dict[str, Any]) -> None:
    """同一 URL 可能因 pages.json 按 (job_id, url) 存而出现多条：**最新那条说了算**。"""
    key = str(url)
    current = mapping.get(key)
    if current is None or _newer_than(value, current):
        mapping[key] = value


def _known_map(known: Any) -> dict[str, dict[str, Any]]:
    """known 统一成 url → CrawlResult；list 与 dict 两种入参都认，重复 url 取最新记录。"""
    mapping: dict[str, dict[str, Any]] = {}
    if isinstance(known, dict):
        for key, value in known.items():
            if not isinstance(value, dict):
                continue
            url = value.get("url") or key
            if url:
                _put_known(mapping, url, value)
        return mapping
    for value in known or []:
        if isinstance(value, dict) and value.get("url"):
            _put_known(mapping, value["url"], value)
    return mapping


def plan(job: dict[str, Any], known: Any, *, mode: str = "incremental",
         recheck_after_days: float = 30, force: bool = False,
         now: datetime | str | None = None) -> dict[str, Any]:
    """增量计划：known（pages.json 的 CrawlResult 列表）里够新的成功记录跳过。

    - mode="incremental"（默认）：fresh 的跳过，其余 todo；
    - mode="full" 或 force=True：全部重抓，skip 为空（mode 返回 "full"）；
    - `known` 可以是 list[dict]，也可以是 {url: dict} 映射；
    - 只读 job，不改 items / status；`now` 可注入，便于测试时间边界。
    """
    _require_job(job)
    if mode not in PLAN_MODES:
        raise ValueError(f"mode 只能是 {PLAN_MODES}，收到：{mode!r}")
    try:
        days = float(recheck_after_days)
    except (TypeError, ValueError) as exc:
        raise ValueError("recheck_after_days 必须是数字") from exc
    if not math.isfinite(days) or days < 0:
        raise ValueError("recheck_after_days 必须是非负有限数")
    if now is None:
        now_dt = datetime.now().astimezone()
    else:
        now_dt = _parse_ts(now)
        if now_dt is None:
            raise ValueError(f"now 无法解析成时间：{now!r}")

    urls = _job_urls(job)
    if force or mode == "full":
        return {"todo": list(urls), "skip": [], "mode": "full"}

    known_map = _known_map(known)
    todo: list[str] = []
    skip: list[dict[str, str]] = []
    for url in urls:
        reason = _skip_reason(known_map.get(url), now_dt, days)
        if reason is None:
            todo.append(url)
        else:
            skip.append({"url": url, "reason": reason})
    return {"todo": todo, "skip": skip, "mode": "incremental"}


# ===== ③ 落盘 =====
def _safe_name(job_id: str) -> str:
    """job_id → 安全文件名：只留字母数字/中文与 -_.，杜绝路径穿越。"""
    text = str(job_id or "").strip()
    if not text:
        raise ValueError("job_id 不能为空")
    safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in text)
    safe = safe.strip(".")
    return safe or "job"


def job_path(job_id: str, directory: str | None = None) -> str:
    """job JSON 路径：默认落在地址簿登记的 ps.JOBS_DIR（不在本模块拼 data/jobs）。"""
    base = directory if directory is not None else ps.JOBS_DIR
    return os.path.join(str(base), _safe_name(job_id) + ".json")


def save_job(job: dict[str, Any], directory: str | None = None) -> str:
    """原子写 job JSON（经 io_utils 的唯一 I/O 口子）；返回落盘路径。"""
    _require_job(job)
    path = job_path(job["job_id"], directory)
    io_utils.write_json(path, job)
    return path


def load_job(job_id: str, directory: str | None = None) -> dict[str, Any] | None:
    """读 job JSON；文件不存在返回 None，内容坏掉抛 ValueError（不静默当空任务）。"""
    path = job_path(job_id, directory)
    if not os.path.isfile(path):
        return None
    try:
        data = io_utils.read_json(path)
    except ValueError as exc:              # JSONDecodeError 是 ValueError 子类
        raise ValueError(f"job 文件不是合法 JSON：{path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"job 文件格式不对（应为 dict）：{path}")
    return data


# ===== ④ 自测 =====
def _self_test() -> None:
    """零网络自检：状态机拦截 / save-load 往返 / 增量计划 / 收口（只写临时目录）。"""
    import tempfile

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    created = new_job("job-1", ["https://a.cn/1", "https://a.cn/1", "https://b.cn/2"],
                      project_id="p1", request={"urls": ["https://a.cn/1"]})
    assert created["status"] == "queued", created["status"]
    assert created["urls"] == ["https://a.cn/1", "https://b.cn/2"], created["urls"]
    assert created["items"]["https://a.cn/1"]["status"] == "pending"
    assert progress(created) == {"total": 2, "done": 0, "ok": 0, "failed": 0,
                                 "skipped": 0, "percent": 0.0}, progress(created)

    with tempfile.TemporaryDirectory() as tmp:
        path = save_job(created, tmp)
        assert os.path.isfile(path), path
        loaded = load_job("job-1", tmp)
        assert loaded == created, "save/load 往返字段不丢"
        assert load_job("没有这个 job", tmp) is None

    # 增量计划：a.cn/1 一小时前抓过且有指纹 → 跳过；其余照抓
    known = [{"url": "https://a.cn/1", "status": "ok",
              "fetched_at": "2026-10-20T11:00:00+00:00", "content_sha256": "sha"}]
    planned = plan(loaded, known, mode="incremental", recheck_after_days=30, now=now)
    assert planned["todo"] == ["https://b.cn/2"], planned
    assert planned["skip"] == [{"url": "https://a.cn/1", "reason": "fresh"}], planned
    assert plan(loaded, known, mode="full", now=now)["todo"] == loaded["urls"]

    # 状态机：合法路径 + 非法流转
    transition(loaded, "running")
    assert loaded["status"] == "running" and loaded["started_at"]
    try:
        transition(loaded, "queued")
    except ValueError:
        pass
    else:
        raise AssertionError("running → queued 应判非法")

    mark(loaded, "https://a.cn/1", {"status": "ok", "record_id": "r1", "error": None})
    mark(loaded, "https://b.cn/2", {"status": "failed", "record_id": "r2",
                                    "error": {"code": "E_NET"}})
    assert loaded["counts"]["ok"] == 1, loaded["counts"]
    assert loaded["counts"]["failed"] == 1, loaded["counts"]
    assert progress(loaded)["percent"] == 100.0, progress(loaded)
    settle(loaded)
    assert loaded["status"] == "partial", loaded["status"]
    assert resume(loaded) == {"todo": []}, resume(loaded)

    done_job = new_job("job-2", ["https://c.cn/3"], started_at=now)
    assert done_job["status"] == "running", done_job["status"]
    mark(done_job, "https://c.cn/3", {"status": "ok", "record_id": "r3"})
    settle(done_job)
    assert done_job["status"] == "done", done_job["status"]
    try:
        transition(done_job, "running")
    except ValueError:
        pass
    else:
        raise AssertionError("done → running 应判非法")

    # 链接发现会往 job 里追加 URL：只加不重、进度立刻重算
    grow = new_job("job-3", ["https://e.cn/1"])
    add_urls(grow, ["https://e.cn/2", "https://e.cn/1"])
    assert grow["urls"] == ["https://e.cn/1", "https://e.cn/2"], grow["urls"]
    assert grow["items"]["https://e.cn/2"]["status"] == "pending"
    assert progress(grow)["total"] == 2, progress(grow)

    print("[OK] job 自检通过：状态机拦非法流转 / save-load 往返 / 增量计划 / "
          "终态收口（只写临时目录）")


if __name__ == "__main__":
    _self_test()
