# -*- coding: utf-8 -*-
"""CLI 入口层：按顺序把功能模块串起来；研究编排入口为 research_api.py。

《工程架构规范》§01：只有入口层有权横向调用多个功能模块。功能层各模块之间
研究功能模块之间零 import；main.py 与 research_api.py 是显式编排入口。

============================== 函数结构树 ==============================
① 子命令（每个只做编排，具体活都调功能层）
    cmd_probe(argv)      判定：逐个 URL 探测 → 落 site_profile.json → 汇总该用哪个工具
    cmd_check(argv)      只看 robots 合规 + 页面类型（不落盘）
    cmd_crawl(argv)      ① 采集：URL 池 → robots 闸门 → 抓正文 → 快照 + 清单
    cmd_parse(argv)      ②③ 解析：清单 → 文章 → 数字事实
    cmd_status(argv)     体检：各阶段产物有多少、各环节卡在哪
    cmd_model_setup      显式准备固定版本 CPU 向量模型
    cmd_analyze          调 research_api.analyze → 语料/检索/证据/CSV
    cmd_train            调 research_api.train → 审核/可比年度序列/回测
    cmd_research_api     查看独立研究 API 能力
    cmd_ai               可选 DeepSeek 连接检查或限量证据建议
② 派发
    main(argv)           解析子命令名并派发；`--self-test` 跑全部模块自检
    _force_utf8()        终端改 UTF-8（调共用层 console）

============================== 调用关系总览 ==============================
main ──▶ console.force_utf8_output
     ──▶ cmd_probe   ──▶ probe.probe_many                 （判定层：逐条探测）
     │                    └─▶ probe.save_profiles         （写 site_profile.json）
     │                ──▶ route.group_by_tool             （按工具汇总）
     ──▶ cmd_check   ──▶ web_check.check_robots          （合规 + 页面类型）
     ──▶ cmd_crawl   ──▶ crawl.Crawl_Page.crawl_many     （抓正文存快照）
     │                    └─▶ io_utils.write_json        （写 pages.json）
     ──▶ cmd_parse   ──▶ extract.Extract_Article.extract_all
     │                    └─▶ save_outputs               （articles.json / .csv）
     │                ──▶ extract.Extract_Facts.extract_all
     │                    └─▶ save_outputs               （facts.json / .csv）
     ──▶ cmd_status  ──▶ io_utils.read_json              （只读体检，不改任何东西）

============================== 数据流单链路 ==============================
sites\\*.md → [crawl] → data\\raw\\{html,files} + pages.json
           → [parse] → data\\parsed\\articles.* → facts.*
           → [analyze] → data\\research\\（新研究链；parse 是旧兼容命令）

============================== 用法 ==============================
    # 全链路（91 条种子）
    .\\myvenv\\Scripts\\python.exe src\\main.py crawl
    .\\myvenv\\Scripts\\python.exe src\\main.py parse

    # 判定层：先 probe（落画像），再决定每条用哪个抓取档
    .\\myvenv\\Scripts\\python.exe src\\main.py probe              # 全量种子
    .\\myvenv\\Scripts\\python.exe src\\main.py probe --limit 3     # 试水 3 条

    # 只试水 3 条，先看合规和页面类型
    .\\myvenv\\Scripts\\python.exe src\\main.py check --limit 3
    .\\myvenv\\Scripts\\python.exe src\\main.py crawl --limit 3

    # 体检：现在手里有什么
    .\\myvenv\\Scripts\\python.exe src\\main.py status

    # 全部模块零网络自检
    .\\myvenv\\Scripts\\python.exe src\\main.py --self-test

⚠️ 本文件只是编排：**不写任何业务判断**。判定逻辑在功能层/共用层。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from Path_Store import Path_Store as ps
    from webcrawl import (api, boilerplate, console, dedupe, discover, io_utils,
                          job, normalize, pdf, policy, ratelimit, record,
                          source_meta, store, table_ops, web_check)
    from webcrawl import page as crawler
    from webcrawl import probe, route
    from extract import Extract_Article as extractor
    from extract import Extract_Facts as facts_extractor
except ImportError:                     # 直接运行本文件时，把 src 加进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from Path_Store import Path_Store as ps
    from webcrawl import (api, boilerplate, console, dedupe, discover, io_utils,
                          job, normalize, pdf, policy, ratelimit, record,
                          source_meta, store, table_ops, web_check)
    from webcrawl import page as crawler
    from webcrawl import probe, route
    from extract import Extract_Article as extractor
    from extract import Extract_Facts as facts_extractor


# ============================================ ① 终端显示（只在这里打印）
RENDER_LABELS = {"static": "静态 HTML", "dynamic": "JS 动态渲染", "unknown": "未知"}


def _skip_reason(report: dict[str, Any]) -> str:
    """render 为 None 时说明原因：robots 禁止，还是调用方没开探测。"""
    return "robots 禁止，按合规不取页面" if report.get("allowed") is False else "未开启探测"


def _render_headline(report: dict[str, Any]) -> str:
    """把 render 字段折成一行中文；未检测时说明原因。"""
    render = report.get("render")
    if not render:
        return f"未检测（{_skip_reason(report)}）"
    label = RENDER_LABELS.get(render.get("mode"), "未知")
    return (f"{label}；置信度 {render.get('confidence', 0.0):.2f}；"
            f"可见正文 {render.get('text_len', 0)} 字；{render.get('reason') or '无结论'}")


def _evidence_lines(signals: dict[str, Any]) -> list[str]:
    """把命中的证据折成可打印的行；只输出真命中过的证据。"""
    lines: list[str] = []
    if signals.get("spa_mount"):
        lines.append(f"证据       : SPA 挂载点 {signals['spa_mount']}")
    if signals.get("ssr_state"):
        lines.append(f"证据       : 内嵌 SSR 状态 {signals['ssr_state']}")
    if signals.get("noscript_hint"):
        lines.append("证据       : <noscript> 里提示需要 JavaScript")
    if signals.get("js_redirect"):
        lines.append("证据       : JS 跳转 / 反爬脚本（document.cookie 或 location）")
    if signals.get("meta_generator"):
        lines.append(f"证据       : CMS 特征 generator={signals['meta_generator']}")
    return lines


def print_robots(report: dict[str, Any]) -> None:
    """打印 robots.txt 全文 + 判定摘要 + 页面类型。"""
    line = "=" * 78
    tag = {True: "[允许]", False: "[禁止]", None: "[未知]"}.get(report.get("allowed"))
    print(line)
    print(f"站点       : {report['site']}")
    print(f"robots.txt : {report['robots_url']}")
    print(f"HTTP 状态  : {report['status'] if report['status'] is not None else '无响应'}")
    print(f"判定       : {tag}（路径 {report['path']}，命中 {report['rule'] or '无规则'}）")
    print(f"页面类型   : {_render_headline(report)}")
    rules = report.get("rules") or {}
    if rules.get("disallow"):
        print(f"禁止清单   : {', '.join(rules['disallow'][:8])}"
              + (" …" if len(rules["disallow"]) > 8 else ""))
    if rules.get("crawl_delay"):
        print(f"Crawl-delay: {rules['crawl_delay']} 秒")
    print("-" * 78)
    if report.get("content"):
        print(report["content"].rstrip())
    elif report.get("allowed") is not None:
        print("（站点没有 robots.txt）")
    else:
        print(f"未取到内容：{report.get('error')}")
    print(line)
    print()


def print_render_only(report: dict[str, Any]) -> None:
    """精简输出：站点 / 结论 / 置信度 / 正文长度 / 命中的证据。"""
    render = report.get("render")
    print("=" * 78)
    print(f"站点       : {report['site']}")
    if not render:
        print(f"页面类型   : 未检测（{_skip_reason(report)}）")
        print("=" * 78)
        print()
        return
    label = RENDER_LABELS.get(render.get("mode"), "未知")
    print(f"页面类型   : {label}")
    print(f"结论       : {render.get('reason') or '无结论'}")
    print(f"置信度     : {render.get('confidence', 0.0):.2f}")
    print(f"可见正文   : {render.get('text_len', 0)} 字")
    for row in _evidence_lines(render.get("signals") or {}):
        print(row)
    print("=" * 78)
    print()


def print_record(record: dict[str, Any]) -> None:
    """抓取记录的一行摘要。"""
    status = record.get("status")
    mark = {"ok": "[OK]", "ok_pdf": "[PDF]", "robots_blocked": "[禁止]",
            "fetch_failed": "[失败]"}.get(status, "[?]")
    if status in ("ok", "ok_pdf"):
        detail = (f"{record.get('bytes')} 字符 / {record.get('encoding')} / "
                  f"{os.path.basename(record['file']) if record.get('file') else '未保存'}")
    else:
        detail = record.get("robots_rule") or record.get("error") or ""
    print(f"      {mark} {status}  {detail}")


# ============================================ ② 子命令（只编排）
def cmd_probe(argv: list[str]) -> int:
    """判定层：逐个 URL 探测 → 落 site_profile.json → 汇总每条该用哪个工具。

    判定规则与选档表见 docs\\抓取策略表.md；本函数只负责编排与显示。
    """
    parser = argparse.ArgumentParser(prog="main.py probe",
                                     description="探测判定 + 选抓取工具")
    parser.add_argument("urls", nargs="*", help="目标 URL；不给则读种子清单")
    parser.add_argument("--limit", type=int, default=0, help="只探前 N 条，0 = 全部")
    parser.add_argument("--timeout", type=float, default=8.0, help="单次请求超时（秒）")
    parser.add_argument("--delay", type=float, default=1.0, help="请求间隔（秒）")
    parser.add_argument("--no-save", action="store_true", help="只探测不落盘")
    args = parser.parse_args(argv)

    targets = args.urls or io_utils.load_seed_urls(args.limit)
    if not targets:
        print("没有目标 URL：命令行传 URL，或检查 sites\\ 下的种子清单。")
        return 1

    print(f"探测 {len(targets)} 条，超时 {args.timeout}s，间隔 {args.delay}s\n")

    def on_progress(index: int, total: int, profile: dict[str, Any]) -> None:
        print(f"[{index}/{total}] {profile['verdict']:<15} {profile['url']}")
        print(f"        {profile['reason']}")

    profiles = probe.probe_many(targets, progress=on_progress,
                                sleep_s=args.delay, timeout=args.timeout)

    print()
    _print_probe_summary(profiles)

    if not args.no_save:
        probe.save_profiles(profiles, ps.SITE_PROFILE_JSON)
        print(f"\n画像已写入：{ps.SITE_PROFILE_JSON}")
    return 0


def _print_probe_summary(profiles: list[dict[str, Any]]) -> None:
    """判定分布 + 按工具分组：一眼看出该动哪些工具、哪些档位还没装。"""
    counts: dict[str, int] = {}
    for profile in profiles:
        counts[profile["verdict"]] = counts.get(profile["verdict"], 0) + 1

    print("判定分布：")
    for verdict in route.VERDICTS:
        n = counts.get(verdict, 0)
        if not n:
            continue
        decision = route.choose({"verdict": verdict})
        need = "" if decision["available"] else f"（需 {decision['requires']}，暂不可用）"
        print(f"  {verdict:<15} {n:>4} 条 → {decision['tool']:<13} {need}")

    print("\n按工具分组：")
    for tool, urls in sorted(route.group_by_tool(profiles).items()):
        print(f"  {tool:<13} {len(urls):>4} 条")


def cmd_check(argv: list[str]) -> int:
    """只看「能不能爬 + 是静态还是动态」，不落盘。"""
    parser = argparse.ArgumentParser(prog="main.py check", description="只查合规与页面类型")
    parser.add_argument("urls", nargs="*", help="目标 URL；不给则读种子清单")
    parser.add_argument("--limit", type=int, default=3, help="只查前 N 条，默认 3")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--no-render", action="store_true",
                        help="不探测页面类型（默认探测：取页面判静态 / 动态）")
    parser.add_argument("--render-only", action="store_true",
                        help="只判静态 / 动态，不打印 robots.txt 全文")
    args = parser.parse_args(argv)

    targets = args.urls or io_utils.load_seed_urls(args.limit)
    if not targets:
        print("没有目标 URL：命令行传 URL，或检查 sites\\ 下的种子清单。")
        return 1

    probe_render = not args.no_render
    counts: dict[str, int] = {}
    for index, url in enumerate(targets, 1):
        report = web_check.check_robots(url, path=web_check.judge_path_of(url),
                                        timeout=args.timeout, retries=args.retries,
                                        probe_render=probe_render)
        if args.render_only:
            print_render_only(report)
            continue
        print(f"── [{index}/{len(targets)}]")
        print_robots(report)
        counts[report.get("render", {}).get("mode") or "未检测"] = 1

    if args.render_only:
        print(f"共查 {len(targets)} 条")
    return 0


def cmd_crawl(argv: list[str]) -> int:
    """① 采集（契约 v1.2 主路径）：种子 → 判定 → 选档 → 并发抓取 → 规范化 → 去重 → 落盘。"""
    parser = argparse.ArgumentParser(prog="main.py crawl",
                                     description="按契约抓取并规范化")
    parser.add_argument("urls", nargs="*", help="目标 URL；不给则读种子清单")
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 条，0 = 全部")
    parser.add_argument("--delay", type=float, default=1.5, help="同主机请求间隔（秒）")
    parser.add_argument("--timeout", type=float, default=15.0, help="单条超时（秒）")
    parser.add_argument("--job-id", default=None, help="任务号（默认按时间生成）")
    parser.add_argument("--project-id", default=None, help="所属研究项目")
    parser.add_argument("--requested-by", default="cli", help="发起者标识")
    parser.add_argument("--rule-version", default="rules-v0", help="规则版本")
    parser.add_argument("--allow-render", action="store_true", help="允许浏览器档（dynamic）")
    parser.add_argument("--allow-tls", action="store_true", help="允许 TLS 伪装档（blocked）")
    parser.add_argument("--no-save", action="store_true", help="不保存原始材料")
    parser.add_argument("--no-normalize", action="store_true", help="不产出正文/段落/表格")
    parser.add_argument("--workers", type=int, default=4, help="并发数（同主机仍串行）")
    parser.add_argument("--discover", action="store_true",
                        help="展开链接：从种子页发现详情页 / 翻页 / 附件")
    parser.add_argument("--depth", type=int, default=1, help="链接展开层数（0..3，默认 1）")
    parser.add_argument("--max-urls", type=int, default=200, help="本次最多抓多少条（含发现）")
    parser.add_argument("--incremental", action="store_true", help="增量：够新的成功记录跳过")
    parser.add_argument("--recheck-days", type=float, default=30.0, help="增量复核周期（天）")
    parser.add_argument("--force", action="store_true", help="忽略增量，全部重抓")
    parser.add_argument("--sitemap", action="store_true", help="从 robots/sitemap.xml 补充候选")
    parser.add_argument("--no-dedupe", action="store_true", help="不做跨 URL 去重")
    parser.add_argument("--no-job", action="store_true", help="不落任务账本（纯同步用法）")
    parser.add_argument("--no-proxy", action="store_true", help="不走系统代理（国内站常需要）")
    args = parser.parse_args(argv)

    targets = args.urls or io_utils.load_seed_urls(args.limit)
    if not targets:
        print("没有目标 URL：命令行传 URL，或检查 sites\\ 下的种子清单。")
        return 1

    job_id = args.job_id or time.strftime("job_%Y%m%d_%H%M%S")
    print(f"任务 {job_id}：目标 {len(targets)} 条，并发 {args.workers}，"
          f"间隔 {args.delay}s，发现 {'开' if args.discover else '关'}\n")

    def on_progress(done: int, total: int) -> None:
        if total <= 20 or done == total or done % 10 == 0:
            print(f"  … 已完成 {done}/{total}", flush=True)

    result = api.crawl({
        "job_id": job_id,
        "project_id": args.project_id,
        "urls": targets,
        "timeout_s": args.timeout,
        "per_host_delay_s": args.delay,
        "respect_robots": True,
        "allow_render": args.allow_render,
        "allow_tls_impersonate": args.allow_tls,
        "save_raw": not args.no_save,
        "normalize": not args.no_normalize,
        "requested_by": args.requested_by,
        "rule_version": args.rule_version,
        "max_workers": args.workers,
        "discover": args.discover,
        "max_depth": args.depth,
        "max_urls": args.max_urls,
        "incremental": args.incremental,
        "recheck_after_days": args.recheck_days,
        "force": args.force,
        "use_sitemap": args.sitemap,
        "dedupe": not args.no_dedupe,
        "save_job": not args.no_job,
        "proxies": {} if args.no_proxy else None,
        "progress": on_progress,
    })

    if result.get("error"):
        error = result["error"]
        print(f"[失败] {error['code']} {error['message']}")
        return 1

    for index, item in enumerate(result["results"], 1):
        mark = {"ok": "[OK]", "ok_pdf": "[PDF]", "skipped": "[跳过]", "failed": "[失败]"}.get(
            item["status"], "[?]")
        detail = item.get("error", {}).get("code") if item.get("error") else ""
        print(f"[{index}/{len(result['results'])}] {mark} {item['status']:8} "
              f"{item.get('verdict', ''):15} {item['url']}")
        if detail:
            print(f"          {detail}  {item['error']['message']}")
        elif item["status"] == "ok":
            meta = item.get("meta") or {}
            print(f"          正文 {item['text_chars']} 字 / {item['paragraph_count']} 段 / "
                  f"表格 {item['text_meta']['table_count']} 张（挡掉版式表 "
                  f"{meta.get('tables_dropped', 0)}）/ "
                  f"{(item.get('source') or {}).get('publisher') or '来源主体未识别'}")

    counts = result["counts"]
    print("\n合计：" + " / ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    fresh = result.get("fresh") or []
    if fresh:
        print(f"增量跳过 {len(fresh)} 条（{args.recheck_days:g} 天内已抓过且内容未过期）")
    dedupe_info = result.get("dedupe") or {}
    if dedupe_info.get("duplicate_count"):
        print(f"重复 {dedupe_info['duplicate_count']} 条（已打 duplicate_of 标记，"
              f"未删任何记录）/ 检查 {dedupe_info.get('checked', 0)} 条")
    progress = result.get("progress") or {}
    if progress:
        print(f"任务状态 {result.get('job_status')}：{progress.get('done')}/{progress.get('total')} "
              f"({progress.get('percent')}%)；账本 {ps.JOBS_DIR}")
        left = int(progress.get("total") or 0) - int(progress.get("done") or 0)
        if left > 0:
            print(f"⚠ 还有 {left} 条没尝试（本次 --max-urls={args.max_urls} 预算用尽，"
                  f"或发现出来的 URL 未跑完）")
            print(f"  继续跑：python src\\main.py resume {job_id}")
    print(f"清单已写入：{ps.PAGES_INDEX_JSON}")
    return 0


def cmd_resume(argv: list[str]) -> int:
    """续跑一个没跑完的任务：只抓账本里还 pending 的 URL。"""
    parser = argparse.ArgumentParser(prog="main.py resume", description="续跑任务")
    parser.add_argument("job_id", help="任务号")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    info = api.job_progress(args.job_id)
    if info.get("error"):
        print(f"[失败] {info['error']['code']} {info['error']['message']}")
        return 1
    print(f"任务 {args.job_id} 当前 {info['status']}："
          f"{info['progress']['done']}/{info['progress']['total']} 已完成")
    if info["status"] in ("done", "cancelled"):
        print("任务已终态，无需续跑；要重跑请换 job-id 或加 --force。")
        return 0
    result = api.resume_job(args.job_id, {"max_workers": args.workers})
    if result.get("error"):
        print(f"[失败] {result['error']['code']} {result['error']['message']}")
        return 1
    print("合计：" + " / ".join(f"{k} {v}" for k, v in sorted(result["counts"].items())))
    print(f"收口状态：{result.get('job_status')}")
    return 0


def cmd_job(argv: list[str]) -> int:
    """看任务账本：状态 / 进度 / 各条结果（只读）。"""
    parser = argparse.ArgumentParser(prog="main.py job", description="任务进度")
    parser.add_argument("job_id", nargs="?", help="不给则列出全部任务")
    args = parser.parse_args(argv)

    if args.job_id:
        info = api.job_progress(args.job_id)
        if info.get("error"):
            print(f"[失败] {info['error']['code']} {info['error']['message']}")
            return 1
        import json
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    jobs = sorted(name[:-5] for name in os.listdir(ps.JOBS_DIR)) \
        if os.path.isdir(ps.JOBS_DIR) else []
    if not jobs:
        print(f"还没有任何任务账本。约定写在：{ps.JOBS_DIR}")
        return 0
    for job_id in jobs:
        info = api.job_progress(job_id)
        progress = info.get("progress") or {}
        print(f"{job_id:32} {str(info.get('status')):9} "
              f"{progress.get('done', 0)}/{progress.get('total', 0)} "
              f"({progress.get('percent', 0)}%)  {info.get('created_at') or ''}")
    return 0


def cmd_expand(argv: list[str]) -> int:
    """只做链接发现：种子页 → 候选池（不抓正文），看能发现多少详情页/附件。"""
    parser = argparse.ArgumentParser(prog="main.py expand",
                                     description="链接发现（不抓正文）")
    parser.add_argument("urls", nargs="*", help="目标 URL；不给则读种子清单")
    parser.add_argument("--limit", type=int, default=5, help="只展开前 N 条，默认 5")
    parser.add_argument("--max-urls", type=int, default=200, help="候选池上限")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--no-proxy", action="store_true")
    args = parser.parse_args(argv)

    targets = args.urls or io_utils.load_seed_urls(args.limit)
    if not targets:
        print("没有目标 URL：命令行传 URL，或检查 sites\\ 下的种子清单。")
        return 1
    result = api.expand({
        "job_id": time.strftime("expand_%Y%m%d_%H%M%S"),
        "urls": targets,
        "timeout_s": args.timeout,
        "max_urls": args.max_urls,
        "follow_pagination": True,
        "follow_attachments": True,
        "proxies": {} if args.no_proxy else None,
    })
    if result.get("error"):
        print(f"[失败] {result['error']['code']} {result['error']['message']}")
        return 1
    for seed in result["seeds"]:
        print(f"  {seed['found']:>4} 条候选  ← {seed['url']}")
    kinds: dict[str, int] = {}
    for item in result["pool"]:
        kinds[item.get("kind") or "?"] = kinds.get(item.get("kind") or "?", 0) + 1
    print(f"\n候选池 {len(result['pool'])} 条：" +
          " / ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    for item in result["pool"][:10]:
        print(f"  [{item.get('kind')}] {item['url']}")
    print(f"\n候选池已写入：{ps.URL_POOL_JSON}")
    return 0


def cmd_plan(argv: list[str]) -> int:
    """看已保存的判定画像会被分到哪个抓取档。"""
    parser = argparse.ArgumentParser(prog="main.py plan", description="判定画像 → 选档")
    parser.add_argument("--profile", default=ps.SITE_PROFILE_JSON, help="画像 JSON 路径")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.profile):
        print(f"找不到画像：{args.profile}\n先跑：python src\\main.py probe")
        return 1

    profiles = list(probe.load_profiles(args.profile).values())
    plans = api.plan(profiles)
    counts: dict[str, int] = {}
    for item in plans:
        counts[item["tool"]] = counts.get(item["tool"], 0) + 1
    for item in plans:
        flag = "可用" if item["available"] else f"缺 {item['requires']}"
        print(f"{item['verdict']:15} → {item['tool']:13} {flag:18} {item['url']}")
    print("\n按档位：" + " / ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 0


def cmd_material(argv: list[str]) -> int:
    """按 record_id 回读一条材料的正文与表格。"""
    parser = argparse.ArgumentParser(prog="main.py material", description="回读材料")
    parser.add_argument("record_id", help="材料记录 ID")
    parser.add_argument("--chars", type=int, default=400, help="正文预览字数")
    args = parser.parse_args(argv)

    material = api.read_material(args.record_id)
    if material.get("error"):
        print(f"[失败] {material['error']['code']} {material['error']['message']}")
        return 1
    print(material["text"][:args.chars])
    if len(material["text"]) > args.chars:
        print(f"…（共 {len(material['text'])} 字）")
    print(f"\n表格 {len(material['tables'])} 张")
    for table in material["tables"]:
        preview = table["csv"].splitlines()[:3]
        print(f"  #{table['index']}：" + " | ".join(preview))
        if table.get("wide_csv"):
            print("      宽表：" + " | ".join(table["wide_csv"].splitlines()[:2]))
    return 0


def cmd_policy(argv: list[str]) -> int:
    """查看 / 生成 / 写入站点访问策略。"""
    parser = argparse.ArgumentParser(prog="main.py policy", description="站点访问策略")
    parser.add_argument("host", nargs="?", help="只看某个主机；不给则看全部")
    parser.add_argument("--from-profile", nargs="?", const="default", default=None,
                        help="从探测画像生成建议策略（省略路径则读 site_profile.json）")
    parser.add_argument("--write", action="store_true",
                        help="把生成的策略真正写入 site_policy.json（人工值不覆盖）")
    parser.add_argument("--overwrite", action="store_true",
                        help="连人工改过的条目也覆盖（危险，默认关）")
    args = parser.parse_args(argv)

    if args.from_profile:
        target = args.from_profile
        if target in ("site_profile.json", "default"):
            target = ps.SITE_PROFILE_JSON
        if not os.path.isfile(target):
            print(f"找不到画像：{target}\n先跑：python src\\main.py probe")
            return 1
        profiles = list(probe.load_profiles(target).values())
        if args.write:
            result = api.write_site_policy(profiles, overwrite=args.overwrite)
            print(f"写入 {len(result['written'])} 条 / 保留人工值 {len(result['kept'])} 条 / "
                  f"跳过 {len(result['skipped'])} 条")
            for host in result["written"]:
                print(f"  + {host}")
            for host in result["kept"]:
                print(f"  = {host}（已存在，保持原样）")
            print(f"\n策略文件：{ps.SITE_POLICY_JSON}")
            return 0
        for profile in profiles:
            suggestion = policy.from_profile(profile)
            flag = "跳过" if suggestion["preferred_tool"] == "skip" else "静态"
            print(f"{suggestion['host']:32} 间隔 {suggestion['min_interval_s']}s  "
                  f"首选档 {flag}  需登录 {suggestion['requires_login']}")
        print("\n（只预览，未落盘；要写入加 --write）")
        return 0

    if args.host:
        import json
        print(json.dumps(api.site_policy(args.host), ensure_ascii=False, indent=2))
        return 0
    policies = api.site_policy()
    if not policies:
        print(f"还没有任何站点策略。约定写在：{ps.SITE_POLICY_JSON}")
        print("生成：python src\\main.py probe  然后  python src\\main.py policy --from-profile --write")
        return 0
    for host, item in sorted(policies.items()):
        print(f"{host:32} 间隔 {item.get('min_interval_s')}s  "
              f"首选档 {item.get('preferred_tool')}  {item.get('note') or ''}")
    return 0


def cmd_parse(argv: list[str]) -> int:
    """②③ 解析：清单 → 文章 → 数字事实（全部离线，只读 snapshot）。"""
    parser = argparse.ArgumentParser(prog="main.py parse", description="清单 → 文章 → 事实")
    parser.add_argument("--pages", default=ps.PAGES_INDEX_JSON, help="抓取清单 JSON")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条，0 = 全部")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.pages):
        print(f"找不到抓取清单：{args.pages}\n先跑：python src\\main.py crawl")
        return 1

    pages = io_utils.read_json(args.pages)
    if args.limit and args.limit > 0:
        pages = pages[:args.limit]

    articles = extractor.extract_all(pages)
    extractor.print_summary(articles)
    outputs = extractor.save_outputs(articles)
    print(f"文章已写入：{outputs['json']}")
    print(f"文章已写入：{outputs['csv']}")

    facts = facts_extractor.extract_all(articles)
    facts_extractor.print_summary(facts)
    outputs = facts_extractor.save_outputs(facts)
    print(f"事实已写入：{outputs['json']}")
    print(f"事实已写入：{outputs['csv']}")
    return 0


def cmd_status(argv: list[str]) -> int:
    """体检：各阶段产物有多少、各环节卡在哪。只读，不改任何东西。"""
    parser = argparse.ArgumentParser(prog="main.py status", description="数据体检")
    parser.parse_args(argv)

    console.force_utf8_output()
    seeds = io_utils.load_seed_urls()
    print(f"种子清单      : {len(seeds)} 条（sites\\ 下 {len(ps.SEED_FILES)} 个文件）")

    html_files = _count_files(ps.RAW_HTML_DIR, ".html")
    pdf_files = _count_files(ps.RAW_FILES_DIR, ".pdf")
    print(f"原始快照      : HTML {html_files} 个 / PDF {pdf_files} 个")

    pages = _read_or_empty(ps.PAGES_INDEX_JSON)
    print(f"抓取清单      : {len(pages)} 条" + ("" if pages else "（还没抓过）"))
    if pages:
        for status in ("ok", "ok_pdf", "robots_blocked", "fetch_failed"):
            n = sum(1 for r in pages if r.get("status") == status)
            if n:
                print(f"  - {status:14}: {n}")
        modes: dict[str, int] = {}
        for record in pages:
            mode = record.get("render_mode")
            if mode:
                modes[mode] = modes.get(mode, 0) + 1
        if modes:
            print("  页面类型      : " + " / ".join(f"{k} {v}" for k, v in sorted(modes.items())))

    articles = _read_or_empty(ps.ARTICLES_JSON)
    total_chars = sum(a.get("text_len") or 0 for a in articles)
    dups = sum(1 for a in articles if a.get("is_duplicate"))
    print(f"旧抽取链      : 文章 {len(articles)} 篇 / 正文合计 {total_chars} 字 / "
          f"转载重复 {dups} 篇  ← 读旧字段，对新记录无效，待退役")

    facts = _read_or_empty(ps.FACTS_JSON)
    print(f"旧事实表      : {len(facts)} 条  ← 同上，待退役")

    # 只数**当前清单真正引用**的表格文件，避免历史遗留文件把数字撑虚
    table_paths = {t.get("csv_path") for page in pages
                   for t in (page.get("tables") or []) if t.get("csv_path")}
    dropped = sum((page.get("meta") or {}).get("tables_dropped") or 0 for page in pages)
    wide = _count_files(ps.WIDE_DIR, ".wide.csv")
    print(f"规范化产物    : 正文 {_count_files(ps.TEXT_DIR, '.txt')} 份 / "
          f"数据表 {len(table_paths)} 张（已挡版式表 {dropped} 张）/ 宽表 {wide} 张")

    jobs = sorted(name[:-5] for name in os.listdir(ps.JOBS_DIR)) \
        if os.path.isdir(ps.JOBS_DIR) else []
    if jobs:
        latest = jobs[-1]
        info = api.job_progress(latest)
        progress = info.get("progress") or {}
        print(f"任务账本      : {len(jobs)} 个；最近 {latest} {info.get('status')} "
              f"{progress.get('done', 0)}/{progress.get('total', 0)}")
    else:
        print("任务账本      : 0 个（还没跑过带账本的 crawl）")

    pool = _read_or_empty(ps.URL_POOL_JSON)
    print(f"链接候选池    : {len(pool)} 条" + ("" if pool else "（还没跑过 expand）"))
    return 0


def _count_files(directory: str, suffix: str) -> int:
    """数一个目录下的某类文件；目录不存在返回 0。"""
    if not os.path.isdir(directory):
        return 0
    return sum(1 for name in os.listdir(directory) if name.endswith(suffix))


def _read_or_empty(path: str) -> list[Any]:
    """读 JSON 清单；文件不存在或读坏了就返回空列表（体检不该因为缺文件而崩）。"""
    if not os.path.isfile(path):
        return []
    try:
        data = io_utils.read_json(path)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


# ============================================ ② 派发
def cmd_model_setup(argv: list[str]) -> int:
    """显式下载固定版本 CPU 模型；分析命令本身不会下载。"""
    parser = argparse.ArgumentParser(prog="main.py model-setup")
    parser.parse_args(argv)
    from research import assets
    try:
        manifest = assets.prepare_model()
        print(f"CPU 向量模型就绪：{manifest['model_id']} @ {manifest['revision']}")
        return 0
    except (ValueError, OSError) as exc:
        print(str(exc))
        return 1


def cmd_analyze(argv: list[str]) -> int:
    """读取规范化正文，执行检索、证据整理和建模就绪检查。"""
    parser = argparse.ArgumentParser(prog="main.py analyze")
    parser.add_argument("--config", default=ps.RESEARCH_CONFIG)
    parser.add_argument("--index", default=ps.PAGES_INDEX_JSON)
    parser.add_argument("--out", default=ps.RESEARCH_DIR)
    parser.add_argument("--lexical-only", action="store_true")
    args = parser.parse_args(argv)
    import research_api
    print("读取并校验本地材料，开始分段检索…", flush=True)
    result = research_api.analyze({"config_path": args.config, "index_path": args.index,
                                   "output_dir": args.out,
                                   "mode": "lexical" if args.lexical_only else "semantic"})
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["status"] == "error")


def cmd_train(argv: list[str]) -> int:
    """只使用明确批准、同口径的可比序列训练。"""
    parser = argparse.ArgumentParser(prog="main.py train")
    parser.add_argument("--reviews", required=True)
    parser.add_argument("--analysis", default=ps.research_output(ps.RESEARCH_DIR, "analysis"))
    parser.add_argument("--out", default=ps.RESEARCH_DIR)
    args = parser.parse_args(argv)
    import research_api
    result = research_api.train({"analysis_path": args.analysis, "reviews": args.reviews,
                                 "output_dir": args.out})
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["status"] == "error")


def cmd_research_api(argv: list[str]) -> int:
    """打印独立 API 能力，供未来接线核对。"""
    argparse.ArgumentParser(prog="main.py research-api").parse_args(argv)
    import json
    import research_api
    print(json.dumps(research_api.capabilities(), ensure_ascii=False, indent=2))
    return 0


def cmd_ai(argv: list[str]) -> int:
    """显式调用 DeepSeek；默认只检查模型列表，抽取需指定 --suggest。"""
    parser = argparse.ArgumentParser(prog="main.py ai")
    parser.add_argument("--suggest", action="store_true")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--analysis", default=ps.research_output(ps.RESEARCH_DIR, "analysis"))
    parser.add_argument("--out", default=ps.RESEARCH_DIR)
    args = parser.parse_args(argv)
    from research import llm
    import json
    try:
        client = llm.DeepSeekClient()
        if args.suggest:
            data = io_utils.read_json(args.analysis)
            selected = [p for p in data["passages"] if p.get("selected")]
            # 小额验收优先覆盖实际数字候选，避免把调用耗在无数值导航片段。
            numeric_ids = {f.get("passage_id") for f in data["facts"]}
            selected.sort(key=lambda p: p.get("passage_id") not in numeric_ids)
            result = client.suggest(selected, data["manifest"]["config"], limit=args.limit)
            io_utils.write_json(ps.research_output(args.out, "llm_suggestions"), result)
            print(json.dumps({"suggestions": len(result["suggestions"]),
                              "errors": result["errors"], "usage": result["usage"]},
                             ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"available_models": client.list_models()}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1


def main(argv: list[str]) -> int:
    """解析子命令并派发；不给子命令就打印用法。"""
    console.force_utf8_output()
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] == "--self-test":
        return _self_test()

    command, rest = argv[0], argv[1:]
    handlers = {"probe": cmd_probe, "check": cmd_check, "crawl": cmd_crawl,
                "plan": cmd_plan, "material": cmd_material, "policy": cmd_policy,
                "parse": cmd_parse, "status": cmd_status,
                "expand": cmd_expand, "job": cmd_job, "resume": cmd_resume,
                "model-setup": cmd_model_setup, "analyze": cmd_analyze,
                "train": cmd_train, "research-api": cmd_research_api, "ai": cmd_ai}
    handler = handlers.get(command)
    if handler is None:
        print(f"未知子命令：{command}\n可用：{', '.join(handlers)} 或 --self-test")
        return 1
    return handler(rest)


def _self_test() -> int:
    """把各模块的零网络自检串起来跑一遍（web_check 没自测，改用纯函数断言）。"""
    assert web_check.parse_robots("User-agent: *\nDisallow: /x\n")["groups"]
    assert web_check.is_allowed("/x", web_check.parse_robots(
        "User-agent: *\nDisallow: /x\n"))["allowed"] is False
    for module in (crawler, extractor, facts_extractor, probe, route,
                   boilerplate, discover, ratelimit, job, dedupe, table_ops,
                   normalize, policy, store, pdf, source_meta, record, api):
        module._self_test()
    from research import assets, evidence, llm, retrieval, review, trend
    import research_api
    for module in (assets, evidence, llm, retrieval, review, trend, research_api):
        module._self_test()
    print("[OK] 入口层自检通过：robots 判定 + "
          f"{25} 个模块的零网络自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
