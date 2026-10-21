# -*- coding: utf-8 -*-
"""对外 API —— 契约《爬虫标准API接口.md》v1.2 的**唯一实现入口**。

交付给 FastAPI / Worker / CLI 共同调用：

    probe(request)             → {"profiles": Profile[]}
    plan(profiles)             → Plan[]
    crawl(request)             → {"job_id","counts","results","progress",...}
    resume_job(job_id, opt)    → 续跑一个没跑完的 job
    job_progress(job_id)       → 任务进度（只读账本）
    expand(request)            → 只做链接发现（不抓正文），产出候选池
    read_material(rid)         → {"record_id","text","tables"}
    site_policy(host=None)     → SitePolicy | {host: SitePolicy}
    write_site_policy(profiles) → 把探测画像落成站点策略（不覆盖人工值）

================================ 结构树 =================================
① 校验
    validate(request)           契约 §3.2 校验 → Error | None
② 选档与执行
    _pick_fetcher(tool)         档位 → 抓取函数（不可用返回 None + 原因）
    _fetch(url, tool, req)      单次抓取 → (response, 实际用的档, 不可用原因)
    _fetch_with_fallback(...)   沿 route.FALLBACK_OF 降级
③ 记录构造
    _crawl_one(...)             抓一条 → {"record","found","text"}
    _crawl_safe(...)            兜异常版（并发 worker 用）
    _failed_record(...)         任何异常都产出字段齐全的失败记录
④ 任务编排（v1.3 新增）
    _job_request(request)       去掉不可序列化字段，job 账本只存能落 JSON 的
    _limiter_for(req, profiles) 由 robots crawl-delay + 人工策略建限速器
    _sitemap_urls(req, hosts)   robots/sitemap → 候选 URL
    _candidates(found, req)     详情页 / 翻页 / 附件 → 下一层候选
    crawl(request)              并发 + BFS 链接发现 + 增量 + 去重 + 账本
⑤ 对外
    probe / plan / crawl / resume_job / job_progress / expand /
    read_material / site_policy / write_site_policy
⑥ 自测
    _self_test()                零网络（抓取函数全部打桩）

================================ 调用关系 ================================
main.py ──▶ api.crawl ──▶ job.new_job / plan / mark / add_urls / settle
                      ──▶ ratelimit.HostLimiter / run_parallel
                      ──▶ probe（缺画像时）
                      ──▶ route.choose
                      ──▶ net / fetch_tls / fetch_browser
                      ──▶ normalize（正文容器定位 + 表格过滤 + 宽表）
                      ──▶ discover（详情页 / 翻页 / 附件 / sitemap）
                      ──▶ dedupe（跨 URL 去重）
                      ──▶ record.build_* ──▶ store 落盘
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from Path_Store import Path_Store as ps
    from webcrawl import (boilerplate, dedupe, discover, errors, fetch_browser,
                          fetch_tls, html_utils, io_utils, job as jobmod, net,
                          normalize, pdf, policy, probe as prober, ratelimit,
                          record, route, source_meta, store)
except ImportError:                                  # 直接运行本文件（自测入口）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps
    from webcrawl import (boilerplate, dedupe, discover, errors, fetch_browser,
                          fetch_tls, html_utils, io_utils, job as jobmod, net,
                          normalize, pdf, policy, probe as prober, ratelimit,
                          record, route, source_meta, store)

# ---------------------------------------------------------------- 常量区
DEFAULTS = {
    "timeout_s": 15.0,
    "per_host_delay_s": 1.5,
    "respect_robots": True,
    "allow_render": False,
    "allow_tls_impersonate": False,
    "save_raw": True,
    "normalize": True,
    "parse_pdf": True,
    "dedupe_by_hash": True,
    # ---- v1.3：并发 / 发现 / 增量 / 去重（全部可关，默认行为尽量保守）
    "max_workers": 4,
    "proxies": None,
    "discover": False,
    "max_depth": 1,
    "max_urls": 200,
    "max_pages_per_seed": 5,
    "follow_pagination": True,
    "follow_attachments": True,
    "use_sitemap": False,
    "max_sitemap_urls": 50,
    "incremental": False,
    "recheck_after_days": 30,
    "save_job": True,
    "dedupe": True,
    "dedupe_threshold": 0.85,
}

HTML_TYPES = ("html", "xml", "text/plain")
# job 账本要落 JSON：只保留能序列化的顶层字段（进度回调等运行期对象不进账本）
_SERIALIZABLE = (str, int, float, bool, type(None))
_EMPTY_ROBOTS = {"found": False, "allowed": None, "rule": None, "crawl_delay": None}


def _content_kind(content_type: str | None, url: str) -> str:
    lowered = (content_type or "").lower()
    if "pdf" in lowered or url.lower().split("?")[0].endswith(".pdf"):
        return "pdf"
    if not lowered or any(token in lowered for token in HTML_TYPES):
        return "html"
    return "other"


def validate(request: dict[str, Any]) -> dict[str, Any] | None:
    """契约 §3.2 四条校验 + v1.3 并发/发现参数边界。返回 Error 或 None。"""
    if not isinstance(request, dict):
        return errors.make(errors.E_BAD_REQUEST, "request 必须是 dict")
    urls = request.get("urls")
    if not urls or not isinstance(urls, list):
        return errors.make(errors.E_BAD_REQUEST, "urls 必填且必须是非空数组")
    for url in urls:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return errors.make(errors.E_BAD_REQUEST, f"非法 URL：{url!r}")
    if request.get("respect_robots") is False:
        return errors.make(errors.E_BAD_REQUEST, "respect_robots 恒为 true，不接受关闭")
    if not str(request.get("job_id") or "").strip():
        return errors.make(errors.E_BAD_REQUEST, "job_id 必填")
    if float(request.get("timeout_s") or DEFAULTS["timeout_s"]) <= 0:
        return errors.make(errors.E_BAD_REQUEST, "timeout_s 必须大于 0")
    if float(request.get("per_host_delay_s") or 0) < 0:
        return errors.make(errors.E_BAD_REQUEST, "per_host_delay_s 不能为负")
    try:
        workers = int(request.get("max_workers", DEFAULTS["max_workers"]))
        depth = int(request.get("max_depth", DEFAULTS["max_depth"]))
        cap = int(request.get("max_urls", DEFAULTS["max_urls"]))
    except (TypeError, ValueError):
        return errors.make(errors.E_BAD_REQUEST, "max_workers / max_depth / max_urls 必须是整数")
    if workers < 1:
        return errors.make(errors.E_BAD_REQUEST, "max_workers 至少为 1")
    if not 0 <= depth <= discover.MAX_DEPTH:
        return errors.make(errors.E_BAD_REQUEST,
                           f"max_depth 必须在 0..{discover.MAX_DEPTH} 之间")
    if cap < 1:
        return errors.make(errors.E_BAD_REQUEST, "max_urls 至少为 1")
    return None


def _option(request: dict[str, Any], key: str) -> Any:
    value = request.get(key)
    return DEFAULTS[key] if value is None else value


def _pick_fetcher(tool: str) -> tuple[Callable[..., dict[str, Any]] | None, str | None]:
    """档位 → 抓取函数。不可用时返回 (None, 原因)。"""
    if tool in ("static", "static_retry", "pdf"):
        return net.fetch, None
    if tool == "impersonate":
        ok, note = fetch_tls.available()
        return (fetch_tls.fetch_html, None) if ok else (None, note)
    if tool == "browser":
        ok, note = fetch_browser.available()
        return (fetch_browser.fetch_html, None) if ok else (None, note)
    return None, f"档位 {tool} 不执行抓取"


def _fetch(url: str, tool: str, request: dict[str, Any]) -> tuple[dict[str, Any], str, str | None]:
    """抓一次。返回 (response, 实际档位, 不可用原因)。

    proxies 由 request 透传到三个档位：None=环境代理、{}=不走代理、{...}=指定代理。
    """
    fetcher, reason = _pick_fetcher(tool)
    if fetcher is None:
        return ({}, tool, reason)
    timeout = float(_option(request, "timeout_s"))
    proxies = request.get("proxies")
    if tool in ("static", "static_retry", "pdf"):
        response = net.fetch(url, timeout=timeout, proxies=proxies)
    elif tool == "impersonate":
        response = fetcher(url, timeout=timeout, proxies=proxies)
    else:                                            # 浏览器档没有 proxies 参数
        response = fetcher(url, timeout=timeout)
    return (response, tool, None)


def _fetch_with_fallback(url: str, tool: str,
                         request: dict[str, Any]
                         ) -> tuple[dict[str, Any], str, bool, dict[str, Any] | None]:
    """沿 route.FALLBACK_OF 逐级降级，直到拿到响应或降到 skip。"""
    current: str | None = tool
    used: str = tool
    fallback_used = False
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        response, used, reason = _fetch(url, current, request)
        if reason is None and response and not response.get("error"):
            return response, used, fallback_used, None
        if reason is not None and current == tool:
            # 目标档根本不可用（依赖没装 / 未允许）→ 直接记原因，不降级到 skip 之外的档
            return {}, tool, False, errors.make(errors.E_TOOL_UNAVAILABLE, reason)
        next_tool = route.FALLBACK_OF.get(current)
        if next_tool and next_tool != "skip":
            fallback_used = True
            current = next_tool
            continue
        error = (errors.from_status(response.get("status")) if response.get("status")
                 else errors.from_exception(RuntimeError(response.get("error") or "抓取失败")))
        return response or {}, used, fallback_used, error
    return {}, used, fallback_used, errors.make(errors.E_TOOL_FAILED, "降级链走到底仍未成功")


def _job_request(request: dict[str, Any]) -> dict[str, Any]:
    """job 账本只存可 JSON 化的字段（进度回调之类运行期对象不进账本）。"""
    return {key: value for key, value in request.items()
            if isinstance(value, _SERIALIZABLE) or isinstance(value, (list, dict))}


def _elapsed(started: float) -> int:
    return int((time.time() - started) * 1000)


def _failed_record(url: str, request: dict[str, Any], profile: dict[str, Any] | None,
                   message: str, *, depth: int = 0,
                   discovered_from: str | None = None,
                   started: float | None = None) -> dict[str, Any]:
    """任何异常都产出字段齐全的失败记录（下游永远拿到 30 个字段）。"""
    meta = {"requested_by": request.get("requested_by"),
            "rule_version": request.get("rule_version"), "depth": depth}
    if discovered_from:
        meta["discovered_from"] = discovered_from
    return record.build_failed(
        job_id=str(request["job_id"]), url=url,
        verdict=str((profile or {}).get("verdict") or "error"), tool="skip",
        robots=(profile or {}).get("robots") or dict(_EMPTY_ROBOTS),
        error=errors.make(errors.E_TOOL_FAILED, message),
        elapsed_ms=_elapsed(started or time.time()),
        project_id=request.get("project_id"), meta=meta)


def _crawl_one(url: str, request: dict[str, Any], profile: dict[str, Any] | None, *,
               depth: int = 0,
               discovered_from: str | None = None) -> dict[str, Any]:
    """抓一个 URL，返回 {"record": CrawlResult, "found": {...}, "text": str}。

    found 只在 request.discover=True 时填（详情页 / 翻页 / 附件），由 BFS 收走；
    text 给去重用（记录本身不存正文，正文在 normalized/text 盘上）。
    """
    started = time.time()
    job_id = str(request["job_id"])
    project_id = request.get("project_id")
    meta = {"requested_by": request.get("requested_by"),
            "rule_version": request.get("rule_version"), "depth": depth}
    if discovered_from:
        meta["discovered_from"] = discovered_from
    found: dict[str, list[str]] = {"detail": [], "page": [], "attach": []}
    text = ""

    def done(built: dict[str, Any]) -> dict[str, Any]:
        return {"record": built, "found": found, "text": text}

    if profile is None:
        profile = prober.probe(url, timeout=float(_option(request, "timeout_s")))

    robots = profile.get("robots") or dict(_EMPTY_ROBOTS)
    verdict = profile.get("verdict", "error")

    def elapsed() -> int:
        return _elapsed(started)

    # ① 合规闸门：allowed 不是 True 一律不抓页面（契约 §4.3）
    if robots.get("allowed") is not True:
        code = (errors.E_ROBOTS_DISALLOW if robots.get("allowed") is False
                else errors.E_ROBOTS_UNKNOWN)
        return done(record.build_skipped(
            job_id=job_id, url=url, verdict=verdict, tool="skip", robots=robots,
            error=errors.make(code, profile.get("reason") or "robots 不可判定"),
            elapsed_ms=elapsed(), project_id=project_id, meta=meta))

    # ② 登录墙 / 明确跳过
    decision = route.choose(profile)
    tool = str(decision["tool"])
    if tool == "skip":
        code = errors.E_LOGIN_WALL if verdict == "login_wall" else errors.E_ROBOTS_DISALLOW
        return done(record.build_skipped(
            job_id=job_id, url=url, verdict=verdict, tool="skip", robots=robots,
            error=errors.make(code, decision.get("reason") or "按策略跳过"),
            elapsed_ms=elapsed(), project_id=project_id, meta=meta))

    # ③ 未允许的高档位：记不可用，不偷偷降级
    if tool == "browser" and not _option(request, "allow_render"):
        return done(record.build_failed(
            job_id=job_id, url=url, verdict=verdict, tool="skip", robots=robots,
            error=errors.make(errors.E_TOOL_UNAVAILABLE, "判定为 dynamic，本次 allow_render=false"),
            elapsed_ms=elapsed(), project_id=project_id, meta=meta))
    if tool == "impersonate" and not _option(request, "allow_tls_impersonate"):
        return done(record.build_failed(
            job_id=job_id, url=url, verdict=verdict, tool="skip", robots=robots,
            error=errors.make(errors.E_TOOL_UNAVAILABLE,
                              "判定为 blocked，本次 allow_tls_impersonate=false"),
            elapsed_ms=elapsed(), project_id=project_id, meta=meta))

    # ④ 抓取（含降级）
    response, used_tool, fallback_used, error = _fetch_with_fallback(url, tool, request)
    if error is not None or response.get("error"):
        return done(record.build_failed(
            job_id=job_id, url=url, verdict=verdict, tool=used_tool, robots=robots,
            error=error or errors.from_exception(RuntimeError(str(response.get("error")))),
            http_status=response.get("status"), elapsed_ms=elapsed(),
            project_id=project_id, meta=meta))

    raw: bytes = response.get("raw") or b""
    final_url = response.get("final_url") or url
    kind = _content_kind((response.get("headers") or {}).get("content-type"), final_url)
    encoding = response.get("encoding")

    # ⑤ PDF：存原件 + 抽文本层 + 页映射
    if kind == "pdf" or tool == "pdf":
        raw_path = store.save_raw(raw, url, "pdf") if _option(request, "save_raw") else None
        pdf_info = None
        if _option(request, "parse_pdf"):
            parsed = pdf.parse(raw)
            text = parsed["text"]
            pdf_info = {"pages": parsed["pages"], "page_map": parsed["page_map"]}
            if text:
                saved = store.save_pdf_text(text, url)
                pdf_info["text_path"] = record._rel(saved)
        built = record.build_ok(
            job_id=job_id, url=url, final_url=final_url, raw=raw, content_type="pdf",
            encoding=encoding, title=None, verdict="pdf", tool="pdf",
            tool_fallback=fallback_used, robots=robots,
            http_status=response.get("status"), raw_path=raw_path, text=text,
            paragraphs=[], tables=[], pdf=pdf_info, elapsed_ms=elapsed(),
            project_id=project_id, meta=meta)
        if not text:
            built["error"] = errors.make(errors.E_PDF_NO_TEXT, "PDF 无文本层或解析失败")
        return done(built)

    # ⑥ HTML / 其它文本
    html = response.get("text") or ""
    raw_path = store.save_raw(raw, url, "html") if _option(request, "save_raw") else None
    text, paragraphs, tables, text_path, paragraph_file, saved_tables = "", [], [], None, None, []
    normalized: dict[str, Any] = {}
    if _option(request, "normalize") and kind == "html":
        want_html = bool(_option(request, "discover"))
        normalized = normalize.normalize(html, want_html=want_html)
        text, paragraphs, tables = (normalized["text"], normalized["paragraphs"],
                                    normalized["tables"])
        text_path = store.save_text(text, url)
        paragraph_file = store.save_paragraphs(paragraphs, url)
        saved_tables = store.save_tables(tables, url)
        main = normalized.get("main") or {}
        meta["main_kind"] = main.get("kind")
        meta["tables_dropped"] = normalized.get("tables_dropped", 0)
        if _option(request, "discover"):
            found = _found_links(normalized.get("main_html") or html, final_url, request)
            meta["links_found"] = {key: len(value) for key, value in found.items()}

    source = source_meta.extract(html, record.site_of(url), final_url)
    built = record.build_ok(
        job_id=job_id, url=url, final_url=final_url, raw=raw, content_type=kind,
        encoding=encoding, title=html_utils.parse_title(html), verdict=verdict,
        tool=used_tool, tool_fallback=fallback_used, robots=robots,
        http_status=response.get("status"), raw_path=raw_path, text=text,
        paragraphs=paragraphs, tables=saved_tables, source=source,
        text_path=text_path, tables_dir=ps.TABLES_DIR if saved_tables else None,
        paragraph_file=paragraph_file, elapsed_ms=elapsed(),
        project_id=project_id, meta=meta)
    if kind == "other":
        built["error"] = errors.make(errors.E_UNSUPPORTED_TYPE, "内容类型不在 html/pdf 内")
    return done(built)


def _found_links(html: str, base_url: str, request: dict[str, Any]) -> dict[str, list[str]]:
    """在**正文容器内**找下一层候选：详情页 / 翻页 / 附件（都限定同站，附件除外）。"""
    found: dict[str, list[str]] = {"detail": [], "page": [], "attach": []}
    if not html:
        return found
    details = [item.get("url", "") for item in discover.list_detail_links(html, base_url)]
    found["detail"] = [url for url in discover.same_site([u for u in details if u], base_url)
                       if isinstance(url, str)]
    if _option(request, "follow_pagination"):
        pages = discover.next_pages(html, base_url)
        found["page"] = [url for url in discover.same_site(pages, base_url)
                         if isinstance(url, str)]
    if _option(request, "follow_attachments"):
        found["attach"] = [item["url"] for item in discover.attachments(html, base_url)
                           if item.get("url")]
    return found


def _crawl_safe(url: str, request: dict[str, Any], profile: dict[str, Any] | None, *,
                depth: int = 0,
                discovered_from: str | None = None) -> dict[str, Any]:
    """并发的 worker：任何异常都折成失败记录，绝不让一条 URL 拖垮整批。"""
    started = time.time()
    try:
        return _crawl_one(url, request, profile, depth=depth,
                          discovered_from=discovered_from)
    except Exception as exc:                         # noqa: BLE001 —— 单条兜底是刻意的
        return {"record": _failed_record(url, request, profile,
                                         f"{type(exc).__name__}: {exc}", depth=depth,
                                         discovered_from=discovered_from, started=started),
                "found": {"detail": [], "page": [], "attach": []}, "text": ""}


def _limiter_for(request: dict[str, Any],
                 profiles: dict[str, dict[str, Any]]) -> ratelimit.HostLimiter:
    """限速器：default + 人工策略 + robots crawl-delay 取最大（不截断）。"""
    default = float(_option(request, "per_host_delay_s"))
    limiter = ratelimit.HostLimiter(default_s=default)
    hosts: set[str] = set()
    for url in request.get("urls") or []:
        hosts.add(record.site_of(url))
    for profile in profiles.values():
        site = str(profile.get("site") or "").strip()
        if site:
            hosts.add(site.lower())
        delay = (profile.get("robots") or {}).get("crawl_delay")
        if site and delay:
            limiter.set_interval(site, float(delay))
    for host in hosts:
        declared = policy.min_interval(host, default=default)
        if declared > default:
            limiter.set_interval(host, declared)
    return limiter


def _sitemap_urls(request: dict[str, Any], hosts: list[str]) -> list[str]:
    """robots.txt / sitemap.xml → 候选 URL（只在 use_sitemap=True 时调用）。"""
    cap = int(_option(request, "max_sitemap_urls"))
    timeout = float(_option(request, "timeout_s"))
    proxies = request.get("proxies")
    collected: list[str] = []
    for host in hosts:
        candidates: list[str] = []
        robots = net.fetch(f"https://{host}/robots.txt", timeout=timeout,
                           retries=1, proxies=proxies)
        if not robots.get("error") and robots.get("text"):
            candidates.extend(discover.parse_robots_sitemaps(robots["text"]))
        if not candidates:
            candidates.append(f"https://{host}/sitemap.xml")
        for sitemap_url in candidates[:3]:
            response = net.fetch(sitemap_url, timeout=timeout, retries=1, proxies=proxies)
            if response.get("error") or not response.get("text"):
                continue
            parsed = discover.parse_sitemap(response["text"])
            for url in parsed["urls"]:
                if record.site_of(url) == host and url not in collected:
                    collected.append(url)
            for child in parsed["sitemaps"][:2]:     # sitemapindex：再下一层
                sub = net.fetch(child, timeout=timeout, retries=1, proxies=proxies)
                if sub.get("error") or not sub.get("text"):
                    continue
                for url in discover.parse_sitemap(sub["text"])["urls"]:
                    if record.site_of(url) == host and url not in collected:
                        collected.append(url)
    return collected[:cap * max(len(hosts), 1)]


def _candidates(found: dict[str, list[str]], request: dict[str, Any],
                page_url: str) -> list[tuple[str, str]]:
    """一层抓完后攒下一层候选：[(url, 来源页)]，去重保序。

    顺序 = 详情页 → 翻页 → 附件：详情页最先被排进下一层，翻页只取前 N 页
    （max_pages_per_seed），避免一个分页陷阱把预算吃光。
    """
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    buckets = (
        ("detail", list(found.get("detail") or [])),
        ("page", list(found.get("page") or [])[:int(_option(request, "max_pages_per_seed"))]),
        ("attach", list(found.get("attach") or [])),
    )
    for _kind, urls in buckets:
        for url in urls:
            if not url or url in seen or url == page_url:
                continue
            seen.add(url)
            ordered.append((url, page_url))
    return ordered


def _open_job(request: dict[str, Any], seeds: list[str]) -> dict[str, Any] | None:
    """取或建 job 账本；已终态的 job 重跑时重新开一本（旧账本被覆盖）。"""
    if not _option(request, "save_job"):
        return None
    job_id = str(request["job_id"])
    try:
        job = jobmod.load_job(job_id)
    except ValueError:
        job = None
    if job is None or job.get("status") in jobmod.TERMINAL_STATUSES:
        return jobmod.new_job(job_id, seeds, project_id=request.get("project_id"),
                              request=_job_request(request), started_at=datetime.now())
    if job.get("status") != "running":
        jobmod.transition(job, "running")
    jobmod.add_urls(job, seeds)
    return job


def crawl(request: dict[str, Any]) -> dict[str, Any]:
    """按契约抓一批 URL：并发 + 链接发现 + 增量跳过 + 跨 URL 去重 + 账本落盘。

    返回 {"job_id","counts","results","progress","fresh","dedupe","job_status","error"}。
    results 仍是 CrawlResult[]（契约字段不变）；发现的下一层页面已自动并入。
    """
    error = validate(request)
    if error is not None:
        return {"job_id": request.get("job_id") if isinstance(request, dict) else None,
                "counts": {}, "results": [], "progress": {}, "fresh": [],
                "dedupe": None, "job_status": None, "error": error}

    store.ensure_dirs()
    started = time.time()
    job_id = str(request["job_id"])
    seeds = list(dict.fromkeys(request["urls"]))
    profiles = {p.get("url"): p for p in (request.get("profiles") or [])}
    discover_on = bool(_option(request, "discover"))
    max_depth = int(_option(request, "max_depth")) if discover_on else 0
    max_urls = int(_option(request, "max_urls"))
    progress_cb = request.get("progress") if callable(request.get("progress")) else None

    job = _open_job(request, seeds)
    known = store.load_index()
    mode = "incremental" if _option(request, "incremental") else "full"
    # 增量判断只依赖 pages.json，**与是否写账本无关**：save_job=False 时用一本临时账算，
    # 否则关掉账本会把增量能力静默关掉（重抓已抓过的 URL）。
    planning_job = job if job is not None else jobmod.new_job(
        job_id, seeds, project_id=request.get("project_id"),
        request=_job_request(request), started_at=datetime.now())
    planned = jobmod.plan(planning_job, known, mode=mode,
                          recheck_after_days=float(_option(request, "recheck_after_days")),
                          force=bool(request.get("force")))

    todo = list(planned["todo"])
    if request.get("resume") and job is not None:
        pending = set(jobmod.resume(job)["todo"])
        todo = [url for url in todo if url in pending]
    fresh = [item["url"] for item in planned.get("skip") or []]
    if job is not None:
        for url in fresh:
            jobmod.mark(job, url, {"status": "skipped", "record_id": None, "error": None})

    frontier: list[tuple[str, int, str | None]] = [(url, 0, None) for url in todo]
    if _option(request, "use_sitemap"):
        hosts = list(dict.fromkeys(record.site_of(url) for url in seeds))
        sitemap_urls = [url for url in _sitemap_urls(request, hosts)
                        if url not in {item[0] for item in frontier}]
        for url in sitemap_urls:
            frontier.append((url, 0, "sitemap"))
        if job is not None:
            jobmod.add_urls(job, sitemap_urls)   # 账本必须与 frontier 同步，否则 mark 会拒收
    limiter = _limiter_for(request, profiles)
    max_workers = int(_option(request, "max_workers"))

    results: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    visited: set[str] = set()
    for depth in range(max_depth + 1):
        budget = max_urls - len(visited)
        batch = [(url, source) for url, level, source in frontier
                 if level == depth and url not in visited][:max(budget, 0)]
        if not batch:
            break
        urls = [url for url, _ in batch]
        source_of = dict(batch)

        def worker(url: str, level: int = depth) -> dict[str, Any]:
            return _crawl_safe(url, request, profiles.get(url), depth=level,
                               discovered_from=source_of.get(url))

        outputs = ratelimit.run_parallel(urls, worker, max_workers=max_workers,
                                         limiter=limiter, host_of=record.site_of,
                                         progress=progress_cb)
        next_urls: list[str] = []
        for url, output in zip(urls, outputs):
            visited.add(url)
            if "_error" in output:                   # 理论上 _crawl_safe 已兜住，双保险
                output = {"record": _failed_record(url, request, profiles.get(url),
                                                   str(output["_error"]), depth=depth),
                          "found": {}, "text": ""}
            results.append(output["record"])
            texts[url] = output.get("text") or ""
            if job is not None:
                jobmod.mark(job, url, output["record"])
        if discover_on and depth < max_depth:
            for output, url in zip(outputs, urls):
                for candidate, source in _candidates(output.get("found") or {}, request, url):
                    if candidate in visited or candidate in next_urls:
                        continue
                    if len(visited) + len(next_urls) >= max_urls:
                        break
                    next_urls.append(candidate)
                    frontier.append((candidate, depth + 1, source))
            if job is not None:
                jobmod.add_urls(job, next_urls)
        store.upsert_index([output["record"] for output in outputs])
        if job is not None:
            jobmod.save_job(job)

    if job is not None:
        jobmod.settle(job)
        jobmod.save_job(job)

    duplicate_info = _dedupe_results(results, texts, request)
    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    if fresh:
        counts["fresh"] = len(fresh)
    return {"job_id": job_id, "counts": counts, "results": results,
            "progress": jobmod.progress(job) if job is not None else {},
            "job_status": job.get("status") if job is not None else None,
            "fresh": fresh, "dedupe": duplicate_info,
            "elapsed_ms": _elapsed(started), "error": None}


def _dedupe_results(results: list[dict[str, Any]], texts: dict[str, str],
                    request: dict[str, Any]) -> dict[str, Any] | None:
    """跨 URL 去重：只给记录盖 `meta.duplicate_of` 戳，不删任何字节与记录。"""
    if not _option(request, "dedupe") or len(results) < 2:
        return None
    payload: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for item in results:
        record_id = item.get("record_id")
        if item.get("status") not in ("ok", "ok_pdf") or not record_id:
            continue
        text = texts.get(item.get("url") or "")
        if not text:
            continue
        source = item.get("source") or {}
        payload.append({"record_id": record_id, "url": item.get("url"), "text": text,
                        "content_sha256": item.get("content_sha256"),
                        "is_reprint": source.get("is_reprint"),
                        "source": source})
        by_id[record_id] = item
    if len(payload) < 2:
        return None
    found = dedupe.find_duplicates(payload, threshold=float(_option(request, "dedupe_threshold")))
    for group in found.get("groups") or []:
        keeper = group.get("keep")
        for drop in group.get("drops") or []:
            target = by_id.get(drop.get("record_id") or "")
            if target is None:
                continue
            target.setdefault("meta", {})["duplicate_of"] = keeper
            target["meta"]["duplicate_similarity"] = drop.get("similarity")
            target["meta"]["duplicate_reason"] = drop.get("reason")
    return {"groups": found.get("groups") or [],
            "duplicate_count": found.get("duplicate_count", 0),
            "checked": len(payload)}


def resume_job(job_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """续跑一个没跑完的 job：账本里 pending 的 URL 才抓，已完成的跳过。"""
    try:
        job = jobmod.load_job(job_id)
    except ValueError as exc:
        return {"job_id": job_id, "counts": {}, "results": [], "progress": {},
                "fresh": [], "dedupe": None, "job_status": None,
                "error": errors.make(errors.E_BAD_REQUEST, str(exc))}
    if job is None:
        return {"job_id": job_id, "counts": {}, "results": [], "progress": {},
                "fresh": [], "dedupe": None, "job_status": None,
                "error": errors.make(errors.E_BAD_REQUEST, f"找不到任务：{job_id}")}
    request: dict[str, Any] = dict(job.get("request") or {})
    request.update({"job_id": job_id, "urls": list(job.get("urls") or []),
                    "resume": True, "save_job": True})
    request.update(overrides or {})
    return crawl(request)


def job_progress(job_id: str) -> dict[str, Any]:
    """只读任务账本：状态 + 进度（给 CLI / 服务端轮询用）。"""
    try:
        job = jobmod.load_job(job_id)
    except ValueError as exc:
        return {"job_id": job_id, "error": errors.make(errors.E_BAD_REQUEST, str(exc))}
    if job is None:
        return {"job_id": job_id,
                "error": errors.make(errors.E_BAD_REQUEST, f"找不到任务：{job_id}")}
    return {"job_id": job_id, "status": job.get("status"), "mode": job.get("mode"),
            "counts": job.get("counts") or {}, "progress": jobmod.progress(job),
            "created_at": job.get("created_at"), "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"), "error": None}


def expand(request: dict[str, Any]) -> dict[str, Any]:
    """只做链接发现：抓种子页（不解析正文）→ 候选池，落 data/discover/url_pool.json。

    用途：先看清"能从这些种子页发现多少篇详情/附件"，再决定要不要全量抓。
    合规闸门照走：robots 不允许的种子页不会被取。
    """
    error = validate(request)
    if error is not None:
        return {"job_id": request.get("job_id"), "pool": [], "seeds": [],
                "error": error}
    store.ensure_dirs()
    timeout = float(_option(request, "timeout_s"))
    proxies = request.get("proxies")
    cap = int(_option(request, "max_urls"))
    pool: list[dict[str, Any]] = []
    details: dict[str, int] = {}
    kind_of: dict[str, str] = {}
    for url in dict.fromkeys(request["urls"]):
        profile = prober.probe(url, timeout=timeout)
        robots = profile.get("robots") or dict(_EMPTY_ROBOTS)
        if robots.get("allowed") is not True:
            continue
        response = net.fetch(url, timeout=timeout, proxies=proxies)
        if response.get("error") or not response.get("text"):
            continue
        found = _found_links(response["text"], response.get("final_url") or url, request)
        details[url] = sum(len(value) for value in found.values())
        for bucket in ("detail", "page", "attach"):
            for target in found.get(bucket) or []:
                pool.append({"url": target, "source": url, "depth": 1,
                             "kind": bucket})
                kind_of.setdefault(target, bucket)
    merged = discover.merge_pool([], pool, cap)
    for item in merged["pool"]:                    # merge_pool 只保 url/source/depth，补回类型
        item["kind"] = kind_of.get(item.get("url") or "", "unknown")
    io_utils.write_json(ps.URL_POOL_JSON, merged["pool"])
    return {"job_id": request.get("job_id"),
            "seeds": [{"url": url, "found": details.get(url, 0)} for url in details],
            "pool": merged["pool"], "dropped": merged.get("dropped", 0),
            "saved": record._rel(ps.URL_POOL_JSON), "error": None}


def probe(request: dict[str, Any]) -> dict[str, Any]:
    """判定一批 URL。"""
    urls = request.get("urls") or []
    profiles = prober.probe_many(
        list(urls),
        sleep_s=float(request.get("delay_s") or 0),
        timeout=float(request.get("timeout_s") or 8.0))
    if request.get("save", True):
        store.ensure_dirs()
        prober.save_profiles(profiles, ps.SITE_PROFILE_JSON)
    return {"profiles": profiles}


def plan(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """选档：verdict → tool。"""
    plans: list[dict[str, Any]] = []
    for profile in profiles:
        decision = route.choose(profile)
        plans.append({
            "url": profile.get("url"), "verdict": profile.get("verdict"),
            "tool": decision["tool"], "available": decision["available"],
            "requires": decision["requires"], "fallback": decision["fallback"],
            "reason": decision["reason"],
        })
    return plans


def read_material(rid: str) -> dict[str, Any]:
    """按 record_id 回读正文与表格（展示层 / LLM 助手用）。"""
    records = store.load_index()
    target = next((r for r in records if r.get("record_id") == rid), None)
    if target is None:
        return {"record_id": rid, "text": "", "tables": [],
                "error": errors.make(errors.E_BAD_REQUEST, "找不到该 record_id")}

    text = ""
    text_path = (target.get("storage") or {}).get("text_path")
    if text_path:
        full = Path(ps.PROJECT_ROOT) / text_path
        if full.is_file():
            text = io_utils.read_text(str(full))

    tables = []
    for table in target.get("tables") or []:
        csv_path = table.get("csv_path")
        content = ""
        wide = ""
        if csv_path:
            full = Path(ps.PROJECT_ROOT) / csv_path
            if full.is_file():
                content = io_utils.read_text(str(full))
            wide_path = store.wide_of_csv(csv_path)
            if wide_path and Path(wide_path).is_file():
                wide = io_utils.read_text(wide_path)
        tables.append({"index": table.get("index", 0), "csv": content,
                       "wide_csv": wide})
    return {"record_id": rid, "text": text, "tables": tables}


def site_policy(host: str | None = None) -> dict[str, Any]:
    if host:
        found = policy.get_policy(host)
        return found or {"host": host.lower(), "note": "未声明策略"}
    return policy.load_policies()


def write_site_policy(profiles: list[dict[str, Any]], *,
                      overwrite: bool = False) -> dict[str, Any]:
    """把探测画像批量落成站点策略（人工改过的条目默认不动）。"""
    store.ensure_dirs()
    return policy.write_from_profiles(profiles, overwrite=overwrite)


def _self_test() -> None:
    """零网络自检：校验分支 + 一条打桩的成功抓取 + 增量/发现/去重的开关。"""
    assert validate({"urls": ["https://a.cn"], "job_id": "j"}) is None
    assert validate({"urls": [], "job_id": "j"})["code"] == errors.E_BAD_REQUEST
    assert validate({"urls": ["ftp://a.cn"], "job_id": "j"})["code"] == errors.E_BAD_REQUEST
    assert validate({"urls": ["https://a.cn"], "job_id": "j",
                     "respect_robots": False})["code"] == errors.E_BAD_REQUEST
    assert validate({"urls": ["https://a.cn"], "job_id": ""})["code"] == errors.E_BAD_REQUEST
    assert validate({"urls": ["https://a.cn"], "job_id": "j",
                     "max_workers": 0})["code"] == errors.E_BAD_REQUEST
    assert validate({"urls": ["https://a.cn"], "job_id": "j",
                     "max_depth": 9})["code"] == errors.E_BAD_REQUEST

    fake_html = ("<html><head><title>标题</title></head><body><article>"
                 "<p>正文段落一。</p><p>正文段落二。</p></article></body></html>")
    original = globals()["net"]
    calls: list[str] = []

    class _FakeNet:
        DEFAULT_TIMEOUT = 15.0
        DEFAULT_RETRIES = 2
        DEFAULT_DELAY = 1.5
        DEFAULT_USER_AGENT = "test"

        @staticmethod
        def fetch(url: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(url)
            return {"url": url, "final_url": url, "status": 200, "found": True,
                    "text": fake_html, "raw": fake_html.encode("utf-8"),
                    "encoding": "utf-8", "headers": {"content-type": "text/html"},
                    "error": None, "transient": False}

    original_store = (store.save_raw, store.save_text, store.save_paragraphs,
                      store.save_tables, store.upsert_index, store.ensure_dirs,
                      store.load_index)
    saved_jobs: list[dict[str, Any]] = []
    original_job_io = (jobmod.save_job, jobmod.load_job)
    try:
        globals()["net"] = _FakeNet
        store.save_raw = lambda *a, **k: None                       # type: ignore[assignment]
        store.save_text = lambda *a, **k: None                      # type: ignore[assignment]
        store.save_paragraphs = lambda *a, **k: None                # type: ignore[assignment]
        store.save_tables = lambda *a, **k: []                      # type: ignore[assignment]
        store.upsert_index = lambda records: len(records)           # type: ignore[assignment]
        store.ensure_dirs = lambda: None                            # type: ignore[assignment]
        store.load_index = lambda: []                               # type: ignore[assignment]
        def _save_job_stub(job, directory=None):       # type: ignore[assignment]
            saved_jobs.append(job)
            return ""
        jobmod.save_job = _save_job_stub
        jobmod.load_job = lambda job_id, directory=None: None       # type: ignore[assignment]

        profile = {"url": "https://a.cn/x", "site": "a.cn", "verdict": "static",
                   "robots": {"found": True, "allowed": True, "rule": "Allow: /",
                              "crawl_delay": None}}
        result = crawl({"job_id": "j1", "urls": ["https://a.cn/x"], "profiles": [profile],
                        "per_host_delay_s": 0})
        assert result["error"] is None, result
        item = result["results"][0]
        assert item["status"] == "ok", item
        assert item["content_sha256"] and len(item["content_sha256"]) == 64, item
        assert item["paragraph_count"] >= 1, item
        assert calls == ["https://a.cn/x"], calls
        assert result["job_status"] in ("done", "partial", "running"), result["job_status"]
        assert result["progress"]["total"] == 1, result["progress"]
        assert saved_jobs, "job 账本没落盘"

        # 增量：已知 fresh 的 URL 直接跳过，不发请求
        fresh_record = {"url": "https://a.cn/x", "status": "ok",
                        "content_sha256": "x" * 64, "fetched_at": record.now_iso()}
        store.load_index = lambda: [fresh_record]               # type: ignore[assignment]
        calls.clear()
        again = crawl({"job_id": "j2", "urls": ["https://a.cn/x"],
                       "profiles": [profile], "per_host_delay_s": 0,
                       "incremental": True})
        assert again["fresh"] == ["https://a.cn/x"], again["fresh"]
        assert calls == [], calls

        # 关掉 job 账本也能跑（纯函数式用法），且**增量仍然生效**
        fresh_record = {"url": "https://a.cn/x", "status": "ok",
                        "content_sha256": "x" * 64, "fetched_at": record.now_iso()}
        store.load_index = lambda: [fresh_record]               # type: ignore[assignment]
        calls.clear()
        plain_fresh = crawl({"job_id": "j4", "urls": ["https://a.cn/x"],
                             "profiles": [profile], "per_host_delay_s": 0,
                             "save_job": False, "incremental": True})
        assert plain_fresh["fresh"] == ["https://a.cn/x"], plain_fresh["fresh"]
        assert calls == [], calls
        store.load_index = lambda: []                               # type: ignore[assignment]
        plain = crawl({"job_id": "j3", "urls": ["https://a.cn/x"], "profiles": [profile],
                       "per_host_delay_s": 0, "save_job": False})
        assert plain["job_status"] is None and plain["progress"] == {}, plain
    finally:
        globals()["net"] = original
        (store.save_raw, store.save_text, store.save_paragraphs,
         store.save_tables, store.upsert_index, store.ensure_dirs,
         store.load_index) = original_store
        (jobmod.save_job, jobmod.load_job) = original_job_io

    print("[OK] api 自检通过：校验 / 打桩抓取 / 账本 / 增量跳过 / 关账本（零网络）")


if __name__ == "__main__":
    _self_test()
