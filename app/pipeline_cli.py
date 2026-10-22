"""无 SQL 的 CLI 显式聚合入口。

结构：read_urls → collect → _offline → summarize → run；main 仅解析参数/两次用户输入。
调用：启动脚本/02_命令行/启动CLI.bat / app.cli → main → run → 受限采集/隔离本地模型/API建议助手。
数据流：网址TXT+一句需求 → 文件缓存 → 本地整理/可选WGAN/XGBoost → output/cli 分类覆写产物。
CSV先落盘，再调用无记忆LLM清洗；最终summarize独立调用API。不使用SQL。
"""

import argparse
import hashlib
import time
from datetime import datetime
from urllib.parse import urlsplit
from uuid import uuid4

from filelock import FileLock, Timeout
from pydantic import ValidationError

from app.advisor.integrated import IntegratedAdvisor
from app.common.collection import save_collection
from app.common.component import run_component
from app.common.evidence_files import stage_records
from app.common.file_io import (
    read_bytes,
    read_csv_rows,
    read_json,
    read_text,
    reset_pipeline_outputs,
    write_csv_rows,
    write_json,
    write_text,
)
from app.common.gan_config import load_gan_settings
from app.common.paths import (
    CLI_CONFIG,
    CLI_LOCK,
    CLI_OUTPUT_ROOT,
    PROJECT_ROOT,
    DataPaths,
    PipelinePaths,
    project_input,
    url_list_input,
)
from app.common.storage import Storage
from app.contracts.crawler import CrawlRequest, SitePolicy
from app.contracts.pipeline import CLIConfig
from app.core.config import Settings
from app.core.errors import ServiceError
from app.crawler.base import CrawlContext
from app.crawler.integrated import IntegratedCrawler
from app.pipeline.data_quality import report_lines
from app.pipeline_llm import clean_csv
from app.services.policies import Policies


def read_urls(reference: str, max_urls: int) -> tuple[list[str], list[dict]]:
    """读取用户指定的每行一个网址清单，逐行校验/去重并给出明确跳过原因。"""
    source = url_list_input(reference)
    if source.stat().st_size > 2 * 1024**2:
        raise ValueError("网址清单超过 2 MB，请拆成较小文件。")
    urls, ignored = [], []
    for index, line in enumerate(read_text(source).splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        reason = None
        try:
            CrawlRequest(job_id="validate", urls=[value])
        except ValidationError:
            reason = "不是受支持的公开 http(s) 网址格式"
        if value in urls:
            reason = "重复网址"
        if not reason and len(urls) >= max_urls:
            reason = f"本轮最多 {max_urls} 个网址，其余请下轮处理"
        if reason:
            ignored.append({"line": index, "url": value, "status": "skipped", "reason": reason})
            print(f"[跳过] 第 {index} 行：{reason}", flush=True)
        else:
            urls.append(value)
    return urls, ignored


def collect(urls, config, settings, *, adapter=None, on_event=None):
    """顺序受限采集，不启动数据库；每个失败独立记录并继续后续网址。"""
    storage = Storage(settings.data_dir)
    storage.initialize()
    policies = Policies(storage)
    adapter = adapter or IntegratedCrawler(settings.data_dir, settings.bridge_timeout_s)
    records, files, evidence, accesses, total = [], [], [], [], 0
    request = CrawlRequest(job_id="cli-" + uuid4().hex, project_id="cli",
                           urls=urls, timeout_s=20, per_host_delay_s=1.5)
    for index, url in enumerate(urls, 1):
        if on_event:
            on_event({"index": index, "total": len(urls), "url": url, "status": "fetching"})
        print(f"[采集 {index}/{len(urls)}] {url}：检查站点策略、robots、响应与网页类型", flush=True)
        entry = {"url": url, "status": "skipped", "reason": None}
        try:
            try:
                policy = policies.for_url(url)
            except ServiceError as exc:
                if exc.error.code != "E_POLICY_NOT_FOUND":
                    raise
                # 用户逐行提供的网址就是本轮声明范围；已有人工禁令仍优先。
                parts = urlsplit(url)
                policy = SitePolicy(host=parts.hostname, allowed_paths=[parts.path or "/"],
                                    note="CLI 用户明确输入的单页范围；仍检查 robots/登录/拦截")
                print("[范围] 使用清单中该网址的路径；仍遵守 robots 与访问限制", flush=True)
            material = adapter.crawl_one(request, url, CrawlContext(
                settings.data_dir, policy, settings.code_version))
            entry.update(verdict=material.verdict, http_status=material.http_status,
                         record_id=material.record_id)
            print(f"[响应] HTTP {material.http_status} / 类型 {material.content_type} / "
                  f"判定 {material.verdict}", flush=True)
            if material.error or material.status not in {"ok", "ok_pdf"}:
                entry["reason"] = (f"{material.error.code}: {material.error.message}" if material.error
                                   else f"网页类型或访问状态不支持：{material.verdict}")
            else:
                reference = material.storage.get("text_path") or (material.pdf.text_path
                                                                 if material.pdf else None)
                source = storage.resolve_normalized(reference)
                size = source.stat().st_size
                if total + size > config.max_text_bytes:
                    raise ValueError("本轮正文超过笔记本预算，跳过该材料；请拆分网址清单")
                raw = read_bytes(source)
                digest = hashlib.sha256(raw).hexdigest()
                if digest != material.text_sha256:
                    raise ValueError("正文哈希与记录不一致，材料不进入分析")
                total += size
                files.append((reference, source))
                if material.paragraph_file:
                    files.append((material.paragraph_file, storage.resolve_normalized(
                        material.paragraph_file)))
                records.append(material.model_dump(mode="json"))
                evidence.append({"record_id": material.record_id, "url": url, "title": material.title,
                                 "text_sha256": digest, "text": raw.decode("utf-8"),
                                 "published_at": material.source.published_at})
                entry.update(status="ok", reason="允许采集，正文和来源已保存")
        except ServiceError as exc:
            entry["reason"] = f"{exc.error.code}: {exc.error.message}"
        except (OSError, ValueError) as exc:
            entry["reason"] = str(exc)
        print(f"[{entry['status']}] {entry['reason']}", flush=True)
        accesses.append(entry)
        if on_event:
            on_event({**entry, "index": index, "total": len(urls)})
    return records, files, evidence, accesses


def _offline(action, payload, settings, workspace, config):
    """在同一 .venv 子进程执行本地模型，流式打印训练进度且不传 API 密钥。"""
    timeout = (config.gan.timeout_s if action == "cli-model" and not payload.get("plan")
               and config.gan.mode != "off" else config.laptop.timeout_s)
    return run_component({"action": action, **payload, "config": config.model_dump(mode="json")},
                         workspace, settings.data_dir, timeout, stream=True)


def fallback_analysis(question, records, paths, reason):
    """本地模型超时/缺失仍导出材料状态，不把未筛选网页冒充检索命中。"""
    counts = {"documents": len(records), "passages": 0, "selected_passages": 0,
              "candidate_facts": 0, "cleaned_candidates": 0, "excluded_candidates": 0,
              "series_rows": 0}
    result = {"question": question, "counts": counts, "warning": reason,
              "facts": [], "series": [], "issues": [reason], "truncated": None}
    for name in ("raw", "candidates", "cleaned", "rejected", "review", "information", "series", "passages"):
        write_csv_rows(paths.file(name), [], ["status", "reason"])
    write_json(paths.file("missing"), {"status": "analysis_unavailable", "reason": reason})
    write_json(paths.file("analysis"), result)
    return result


def _summary(analysis, modeling):
    """构造有界实际指标供 LLM 使用；不把未来预测当成来源事实。"""
    reports = []
    for item in modeling.get("series_reports", [])[:3]:
        reports.append({key: item[key] for key in ("scope", "status", "requirements", "test",
                       "forecast", "selected_model", "evidence_status", "directory") if key in item})
        if item.get("gan"):
            reports[-1]["gan"] = {key: item["gan"][key] for key in ("status", "synthetic_rows",
                                   "requested_synthetic_rows", "user_accepted", "used_for_final_model",
                                   "validation_improved", "reason")
                                   if key in item["gan"]}
    summary = {"counts": analysis["counts"], "truncated": analysis.get("truncated"),
            "status": modeling["status"], "requirements": modeling.get("requirements", []),
            "analysis_warning": analysis.get("warning"),
            "data_diagnosis": (analysis.get("diagnostics") or {}).get("message"),
            "retrieval": analysis.get("retrieval"),
            "llm_cleaning": analysis.get("llm_cleaning"),
            "data_quality_counts": (analysis.get("data_quality") or {}).get("counts"),
            "series": reports, "candidate_evidence_only": True,
            "synthetic_policy": "生成数据只用于训练，不是新证据；验证/测试均为真实观测"}
    import json

    while len(json.dumps(summary, ensure_ascii=False)) > 2700 and summary["series"]:
        summary["series"].pop()
        summary["series_omitted"] = summary.get("series_omitted", 0) + 1
    return summary


def selected_evidence(paths, evidence):
    """给最终助手匹配原文及机器关联的范围限制，原文哈希仍指向完整来源。"""
    grouped = {}
    for row in read_csv_rows(paths.file("information")):
        text = row.get("text") or ""
        context = row.get("context_evidence")
        flags = row.get("candidate_flags")
        if context and context != "[]":
            text += "\n【本段机器关联的上下文，需结合原文核对】" + context
        if flags and flags != "[]":
            text += "\n【本段候选尚未解决的限制】" + flags
        grouped.setdefault(row.get("record_id"), []).append(text)
    selected = []
    # 按来源均分证据预算，避免第一份长文吃掉全部额度、后续年份无法进入趋势分析。
    per_source = max(400, 12000 // max(1, len(evidence)))
    for item in evidence:
        texts = grouped.get(item["record_id"], [])
        if texts:
            selected.append({**item, "text": "\n\n".join(texts)[:per_source]})
    return selected


def summarize(question, analysis, modeling, evidence, config, settings, paths, *, advisor=None):
    """最后一步可调用 API，失败仍保存 CSV/指标和明确标记的本地状态报告。"""
    summary = _summary(analysis, modeling)
    api = {"status": "disabled", "mode": config.llm, "model": settings.advisor_model,
           "memory_enabled": False, "analysis_scope": "development_trend"}
    content = None
    if config.llm != "off":
        if config.llm == "api" and not settings.advisor_api_key.get_secret_value():
            api.update(status="missing_key", reason="根 .env 未配置 INDUSTRY_ADVISOR_API_KEY")
        else:
            print(f"[分析助手] {'演示模式' if config.llm == 'demo' else 'API'} / "
                  f"{settings.advisor_model}；单独请求，只输出发展趋势", flush=True)
            try:
                result = (advisor or IntegratedAdvisor(settings)).analyze_trend(
                    question, evidence[:32], summary, demo=config.llm == "demo")
                content = result["reply"]["content"]
                api.update(status="completed", reply=result["reply"], warnings=result["warnings"])
            except ServiceError as exc:
                api.update(status="failed", reason=f"{exc.error.code}: {exc.error.message}")
    write_json(paths.file("llm"), api)
    lines = ["# 信息整理与研究结果", "", f"检索需求：{question}", "",
             f"数据处理状态：{modeling['status']}。分析助手：{api['status']}。", "",
             "所有自动抽取均为未人工复核候选。预测为探索性结果；合成样本不代表新增产业证据。", "",
             f"匹配段落 {analysis['counts']['selected_passages']}；数值候选 "
             f"{analysis['counts']['candidate_facts']}；可比年度行 {analysis['counts']['series_rows']}。", "",
             f"匹配信息见 {paths.file('information').relative_to(paths.root).as_posix()}，"
             f"清洗结果见 {paths.file('cleaned').relative_to(paths.root).as_posix()}，"
             f"排除理由见 {paths.file('rejected').relative_to(paths.root).as_posix()}。", ""]
    cleaning = analysis.get("llm_cleaning") or {}
    if analysis.get("data_quality"):
        lines.extend(report_lines(analysis["data_quality"]))
    if cleaning and cleaning.get("status") == "disabled" and (
            analysis.get("extraction") or {}).get("mode") == "deterministic":
        lines.extend([f"数据抽取路径：确定性规则。{cleaning.get('reason', '')}", ""])
    elif cleaning:
        lines.extend([f"LLM清洗状态：{cleaning.get('status')}。{cleaning.get('reason', '')}", "",
                      f"清洗前CSV：{paths.file('llm_input').relative_to(paths.root).as_posix()}；"
                      f"待复核：{paths.file('review').relative_to(paths.root).as_posix()}。", ""])
    for item in modeling.get("series_reports", []):
        lines.extend([f"## {item['scope']}", "", f"状态：{item['status']}", ""])
        lines.extend(f"- {reason}" for reason in item.get("requirements", []))
        if item.get("test"):
            lines.extend(["", "|真实测试方案|MAE|RMSE|R²|样本数|", "|---|---|---|---|---|"])
            for label, key in [("所选模型", "selected"), ("仅真实数据XGBoost", "real_only"), ("沿用上期值", "naive")]:
                values = item["test"].get(key)
                if values:
                    r2 = "不适用" if values.get("r2") is None else f"{values['r2']:.3f}"
                    lines.append(f"|{label}|{values['mae']:.3f}|{values['rmse']:.3f}|{r2}|{values['n']}|")
            if not item["test"]["beats_naive"]:
                lines.extend(["", "真实测试未优于沿用上期值，暂不建议依赖该模型作预测判断。"])
            if item.get("forecast"):
                lines.extend(["", f"探索性预测：{item['forecast']['target_period']}年，{item['forecast']['prediction']:.3f}；不是来源事实。"])
            lines.extend(["", f"模型、预测 CSV 与性能图：{item['directory']}", ""])
    diagnostics = analysis.get("diagnostics") or {}
    if diagnostics:
        lines.extend(["## 数据诊断", "", diagnostics["message"], ""])
        lines.extend(f"- {item['message']}：{item['count']}条" for item in diagnostics.get("rejection_reasons", []))
        reference = paths.file("diagnostics").relative_to(paths.root).as_posix()
        lines.extend(["", f"详细词表、预算及召回方式见 {reference}。", ""])
    if modeling.get("gan_gate"):
        lines.extend([modeling["gan_gate"]["reason"], ""])
    lines.extend(modeling.get("requirements", []))
    lines.extend(["", "## 发展趋势", "", content or (
        "本轮未取得发展趋势分析。" + str(api.get("reason", "")))])
    write_text(paths.file("report"), "\n".join(lines) + "\n")
    print(f"[分析助手] {api['status']}" + (f"：{api['reason']}" if api.get("reason") else ""), flush=True)
    if content:
        print("\n" + content, flush=True)
    return api


def choose_gan(config, input_fn):
    """只有需要增强时由 run 调用；接收框架简写，其余参数完整保留在 JSON。"""
    choice = input_fn("[WGAN] 真实数据通过门槛。生成量=真实训练集×5。选择 1=PyTorch、2=TensorFlow(tf)、"
                      "0=不训练GAN [默认0]：").strip().lower()
    while choice not in {"", "1", "2", "0", "pytorch", "tensorflow", "tf"}:
        choice = input_fn("请输入 1、2 或 0：").strip().lower()
    if choice in {"", "0"}:
        config.gan.mode = "off"
    else:
        config.gan.backend = "tensorflow" if choice in {"2", "tf", "tensorflow"} else "pytorch"
    print(f"[WGAN配置] {config.gan.model_dump(mode='json')}", flush=True)


def accept_gan(modeling, paths, input_fn):
    """生成后打印实际t-SNE和质量结果，再要求明确采用；默认拒绝，不重新训练。"""
    available = False
    for report in modeling.get("series_reports", []):
        gan = report.get("gan", {})
        print(f"[GAN审查] {report['scope']}：{gan.get('status')}；{gan.get('synthetic_rows', 0)}条", flush=True)
        if gan.get("tsne", {}).get("status") == "completed":
            series_paths = PipelinePaths(paths.root / report["directory"], organized=paths.organized)
            reference = series_paths.file("tsne").relative_to(paths.root).as_posix()
            print(f"[t-SNE图片] {reference}（位于本轮结果目录下）", flush=True)
        available = available or gan.get("status") == "accepted"
    if not available:
        print("[GAN] 没有通过初步检查的数据；仅用真实数据继续", flush=True)
        return False
    print("[提示] 查看t-SNE及quality.json；点云重叠不证明数据可靠。", flush=True)
    choice = input_fn("是否采用通过检查的GAN数据进入XGBoost？1=采用，0=仅真实数据 [默认0]：").strip()
    while choice not in {"", "0", "1"}:
        choice = input_fn("请输入1或0：").strip()
    return choice == "1"


def choose_after_crawl(input_fn):
    """采集完成后选择去向；教材分支目前只保存材料，不启动后续处理。"""
    choice = input_fn("[采集后分支] 1=继续原自动流程，2=教材分支（只保存材料并停止）[默认2]：").strip()
    while choice not in {"", "1", "2"}:
        choice = input_fn("请输入1或2：").strip()
    return "continue" if choice == "1" else "textbook"


def run(url_file, question, config, settings, *, paths=None, adapter=None, advisor=None,
        input_fn=input, post_crawl="continue"):
    """无数据库全流程；覆盖前先完成输入校验，锁避免并发覆盖或并发训练。"""
    if post_crawl not in {"ask", "continue", "textbook"}:
        raise ValueError("采集后分支必须为 ask/continue/textbook。")
    mode = config.extraction.mode
    custom_paths = paths is not None
    paths = paths or PipelinePaths(CLI_OUTPUT_ROOT / (
        "collection" if post_crawl != "continue" else mode), organized=True)
    question = question.strip()
    if not 1 <= len(question) <= 4000:
        raise ValueError("检索需求需要 1..4000 个字符。")
    urls, ignored = read_urls(url_file, config.max_urls)
    if not urls:
        raise ValueError("文件中没有有效网址，请按每行一个 http(s) 网址填写。")
    CLI_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(CLI_LOCK), timeout=0):
        started = time.perf_counter()
        reset_pipeline_outputs(paths)
        write_json(paths.file("status"), {"status": "running"})
        write_json(paths.file("config"), config.model_dump(mode="json"))
        print("[开始] 检索/XGBoost 为 CPU 档；GAN 完全按 config/wgan.json，不做性能降档", flush=True)
        try:
            shown = paths.root.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            shown = str(paths.root)
        print(f"[路径] 数据抽取：{mode}；结果目录：{shown}", flush=True)
        print("[流程] 采集 → 选择分支；教材分支停在材料，原流程继续整理/建模", flush=True)
        records, files, evidence, access = collect(urls, config, settings, adapter=adapter)
        collection = save_collection(records, evidence, ignored + access, question, config,
                                     settings.data_dir, paths)
        if post_crawl == "ask":
            write_json(paths.file("status"), {"status": "branch_choice"})
            post_crawl = choose_after_crawl(input_fn)
        if post_crawl == "textbook":
            final = {"status": "materials_ready", "extraction": "textbook",
                     "counts": collection["counts"], "llm_status": "not_started",
                     "seconds": round(time.perf_counter() - started, 3)}
            write_json(paths.file("result"), final)
            write_json(paths.file("status"), {"status": "materials_ready"})
            print(f"[教材分支] 已停在采集完成处，未进行解析、清洗或建模。材料包：{paths.file('sources')}",
                  flush=True)
            return final
        write_json(paths.file("status"), {"status": "running"})
        if not custom_paths and paths.root.name == "collection":
            paths = PipelinePaths(CLI_OUTPUT_ROOT / mode, organized=True)
            reset_pipeline_outputs(paths)
            save_collection(records, evidence, ignored + access, question, config, settings.data_dir, paths)
            write_json(paths.file("status"), {"status": "running"})
        workspace = DataPaths(settings.data_dir).research_workspace(uuid4().hex)
        staged, references = stage_records(records, files, workspace)
        try:
            analysis = _offline("cli-analyze", {"records": staged, "question": question,
                                "reference_map": references, "output": str(paths.root),
                                "output_layout": paths.organized}, settings,
                                workspace, config)
        except ServiceError as exc:
            reason = f"{exc.error.code}: {exc.error.message}"
            print(f"[整理未完成] {reason}；保留已采材料并输出状态", flush=True)
            analysis = fallback_analysis(question, records, paths, reason)
        if mode == "deterministic":
            print("[清洗] 确定性路径跳过LLM清洗，直接使用规则候选与本地校验", flush=True)
        else:
            analysis = clean_csv(question, analysis, config, settings, paths, workspace, advisor=advisor)
        payload = {"output": str(paths.root), "output_layout": paths.organized,
                   "forecast_at": datetime.now().astimezone().isoformat()}
        try:
            modeling = _offline("cli-model", {**payload, "plan": True}, settings, workspace, config)
        except ServiceError as exc:
            modeling = {"status": "data_only", "series_reports": [], "needs_gan": False,
                        "requirements": [f"{exc.error.code}: {exc.error.message}"]}
        use_generated = False
        if modeling["needs_gan"]:
            choose_gan(config, input_fn)
            write_json(paths.file("config"), config.model_dump(mode="json"))
            if config.gan.mode != "off":
                try:
                    review = _offline("cli-model", {**payload, "stage": "gan"}, settings, workspace, config)
                    use_generated = accept_gan(review, paths, input_fn)
                except ServiceError as exc:
                    print(f"[GAN未完成] {exc.error.message}；仅使用真实数据继续", flush=True)
        try:
            modeling = _offline("cli-model", {**payload, "use_generated": use_generated}, settings, workspace, config)
        except ServiceError as exc:
            modeling = {"status": "data_only", "series_reports": [],
                        "requirements": [f"{exc.error.code}: {exc.error.message}"]}
        api = summarize(question, analysis, modeling, selected_evidence(paths, evidence), config,
                        settings, paths, advisor=advisor)
        final = {**modeling, "counts": analysis["counts"], "llm_status": api["status"],
                 "extraction": mode, "output_mode": mode,
                 "seconds": round(time.perf_counter() - started, 3)}
        write_json(paths.file("result"), final)
        write_json(paths.file("status"), {"status": "completed", "llm_status": api["status"],
                                          "warning": analysis.get("warning")})
        print(f"[完成] 报告：{paths.file('report')}；数据：{paths.file('information').parent}；"
              "建模成功时另有 series/ 下预测、模型和图表。", flush=True)
        return final


def main(argv=None):
    """解析可选参数，默认只询问文件地址和一句检索需求。"""
    parser = argparse.ArgumentParser(description="本地产业信息研究 CLI：网址文件 → 需求 → output")
    parser.add_argument("--config", default=CLI_CONFIG.relative_to(PROJECT_ROOT).as_posix())
    parser.add_argument("--urls-file", help="可选：每行一个网址的 TXT，支持带空格的绝对地址")
    parser.add_argument("--query", help="可选：一句检索需求；省略时在终端询问")
    parser.add_argument("--after-crawl", choices=["continue", "textbook"],
                        help="无人值守时指定采集后去向；省略则爬完后询问，教材分支仅保存材料")
    parser.add_argument("--extraction", choices=["llm", "deterministic"],
                        help="覆盖配置的抽取路径：llm=LLM自动清洗，deterministic=按规则确定性抽取")
    args = parser.parse_args(argv)
    try:
        config = CLIConfig.model_validate(read_json(project_input(args.config)))
        if args.extraction:
            config.extraction.mode = args.extraction
        config.gan, _ = load_gan_settings(config.gan)
        url_file = args.urls_file or input("网址清单文件地址（每行一个网站）：").strip()
        question = args.query or input("要检索什么信息（一句话）：").strip()
        settings = Settings(api_token="cli-unused-" + uuid4().hex)
        run(url_file, question, config, settings, post_crawl=args.after_crawl or "ask")
    except (ValueError, OSError, ValidationError) as exc:
        raise SystemExit(f"[配置或输入错误] {exc}") from None
    except Timeout:
        raise SystemExit("[任务运行中] 已有 CLI 正在处理，请等待后再运行。") from None
    except ServiceError as exc:
        raise SystemExit(f"[{exc.error.code}] {exc.error.message}；已生成文件仍保留在 output。") from None
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\n[已停止] 请查看 output/cli/<collection|llm|deterministic>/metadata/status.json。") from None


if __name__ == "__main__":
    main()
