"""网页完整管线显式聚合层；结构：validate → execute → collect/route/prepare/gan/model。

调用：API校验；持久Worker → 本入口 → CLI采集/离线桥接/最终助手。
数据流：网址+需求 → 整理及门槛 → 人工选择GAN → t-SNE审查 → 人工采用 → 建模/建议。
每次领取使用独立目录，父结果只读；API不等待训练、不接收任意路径，不自动重复未知付费结果。
"""
from datetime import datetime

from filelock import FileLock, Timeout

from app.common.collection import input_files, save_collection
from app.common.component import run_component
from app.common.evidence_files import stage_records
from app.common.file_io import read_bytes, read_json, read_text, write_bytes, write_json
from app.common.paths import CLI_CONFIG, DataPaths, PipelinePaths
from app.common.progress import ResearchProgress
from app.contracts.pipeline import CLIConfig
from app.contracts.pipeline_web import PipelineRequest
from app.core.errors import ServiceError
from app.pipeline_cli import collect, fallback_analysis, selected_evidence, summarize
from app.pipeline_llm import clean_csv


def parent_result(runtime, request):
    """只引用同项目完成的管线结果，禁止跨类型、跨阶段或跨项目依赖。"""
    operation = runtime.operations.get(request.parent_id, request.project_id)
    if operation["kind"] != "pipeline" or operation["state"] != "done":
        raise ServiceError("E_PIPELINE_PARENT", "上一步尚未完成或任务类型不符。", 409)
    parent = operation["result"]
    if request.stage == "route":
        if parent["stage"] != "branch_choice":
            raise ServiceError("E_PIPELINE_STAGE", "请先完成网页采集，再选择处理分支。", 409)
        return parent
    expected = {"choice"} if request.stage == "gan" else {"choice", "gan_review"}
    if parent["stage"] not in expected or not parent["can_model"]:
        raise ServiceError("E_PIPELINE_STAGE", "数据未达到建模门槛，或操作顺序不正确。", 409)
    if request.stage == "gan" and not parent["gan_available"]:
        raise ServiceError("E_GAN_UNAVAILABLE", "真实训练数据未满足GAN门槛，可以直接用真实数据建模。", 409)
    if request.stage == "model" and request.use_generated and (
            parent["stage"] != "gan_review" or not parent.get("gan_usable")):
        raise ServiceError("E_GAN_NOT_REVIEWED", "请先生成并审查通过检查的GAN数据。", 409)
    return parent


def validate(runtime, payload):
    """提交时只校验，不执行网络或模型；由API调用。"""
    request = PipelineRequest.model_validate(payload)
    if request.stage not in {"prepare", "collect"}:
        parent_result(runtime, request)


def _files(paths):
    """列出本轮可下载产物；只含目录内登记的普通文件，不接受浏览器路径。"""
    files = {}
    series = sorted(paths.series_parent().glob("*")) if paths.series_parent().is_dir() else []
    for group in [paths, *[PipelinePaths(path, organized=paths.organized)
                          for path in series if path.is_dir() and not path.is_symlink()]]:
        for file in group.registered_files():
            if file.is_file() and file.resolve().is_relative_to(paths.root.resolve()):
                reference = file.relative_to(paths.root).as_posix()
                files[reference] = reference
    return files


def _copy_parent(runtime, parent, paths, *, skip_report=False):
    """仅复制父操作登记文件到本令牌目录；旧Worker和其他决策不会互相覆盖。"""
    source = DataPaths(runtime.settings.data_dir).pipeline(
        parent["run_id"], output=parent.get("storage_layout") == "output-v1")
    # 历史平铺结果升级为分类布局；只接受地址簿登记产物，不沿用外部路径。
    target_map = {}
    groups = [source]
    if source.series_parent().is_dir():
        groups.extend(PipelinePaths(path, organized=source.organized)
                      for path in source.series_parent().iterdir()
                      if path.is_dir() and not path.is_symlink() and not path.is_junction())
    for group in groups:
        destination_group = paths if group.root == source.root else paths.series(group.root.name)
        target_map.update(zip(group.registered_files(), destination_group.registered_files()))
    for reference in parent["files"]:
        origin = (source.root / reference).resolve()
        target = target_map.get(source.root / reference)
        if target is None:
            raise ServiceError("E_ARTIFACT_PATH", "父产物未登记，不能继续处理。", 409)
        if skip_report and target == paths.file("report"):
            continue
        destination = target.resolve()
        if not origin.is_relative_to(source.root.resolve()) or not destination.is_relative_to(paths.root.resolve()):
            raise ServiceError("E_ARTIFACT_PATH", "产物引用越界。", 409)
        write_bytes(destination, read_bytes(origin))


def _collection_result(request, paths, config, context, mode, progress):
    """完成采集或教材暂停检查点；不生成分析表、不检查建模门槛、不调用LLM。"""
    stage = "branch_choice" if mode == "collection" else "materials_ready"
    collection = read_json(paths.file("collection"))
    result = {"stage": stage, "run_id": f"{mode}/{paths.root.name}", "extraction": mode,
              "storage_layout": "output-v1", "context": context, "counts": collection["counts"],
              "collection": collection, "can_model": False, "gan_available": False, "gan_usable": False,
              "access": read_json(paths.file("access")),
              "modeling": {"status": "not_started", "series_reports": [], "requirements": []},
              "llm": None, "report": read_text(paths.file("report"))}
    write_json(paths.file("config"), config.model_dump(mode="json"))
    write_json(paths.file("manifest"), {"storage_layout": "output-v1", "run_id": result["run_id"],
        "operation_id": request.operation_id, "parent_id": request.parent_id,
        "project_id": request.project_id, "stage": stage, "extraction": mode,
        "question": context["question"], "counts": result["counts"],
        "created_at": datetime.now().astimezone().isoformat()})
    result["files"] = _files(paths)
    progress.phase(stage, "采集完成，请选择继续原流程或进入教材分支" if stage == "branch_choice"
                   else "已进入教材分支，停在采集材料，等待确定后续解析方式")
    return result


def execute(runtime, claim):
    """Worker执行一个阶段并返回页面所需真实状态；完整模型摘要只在最后传给助手。"""
    request = PipelineRequest.model_validate(claim.request)
    if claim.attempts > 1 and request.stage in {"prepare", "route", "model"}:
        raise ServiceError("E_PIPELINE_OUTCOME_UNKNOWN", "整理/建模/分析执行曾中断，请核对产物后重新提交新操作，避免重复API请求。", 409)
    progress = ResearchProgress(lambda value: runtime.operations.update_progress(claim, value),
                                total=len(request.urls))
    token = claim.token.replace("-", "")
    workspace = DataPaths(runtime.settings.data_dir).research_workspace(token)
    parent = None
    if request.stage in {"prepare", "collect"}:
        mode = "collection" if request.stage == "collect" else request.extraction
        config = CLIConfig.model_validate(read_json(CLI_CONFIG))
        config.llm, config.retrieval = request.llm, request.retrieval
        if request.stage == "prepare":
            config.extraction.mode = mode
    else:
        progress.phase("preparing", "正在读取上一阶段结果")
        parent = parent_result(runtime, request)
        mode = request.extraction if request.stage == "route" else parent.get("extraction") or "llm"
    paths = DataPaths(runtime.settings.data_dir).pipeline(f"{mode}/{token}", output=True)
    if request.stage in {"prepare", "collect"}:
        config.gan.mode = "auto"
        forecast_at = datetime.now().astimezone().isoformat()
        progress.phase("collecting", f"开始采集，共 {len(request.urls)} 个网址")
        records, files, evidence, access = collect(request.urls, config, runtime.settings,
                                                  adapter=runtime.adapter, on_event=progress.crawl)
        write_json(paths.file("access"), access)
        write_json(paths.file("materials"), records)
        write_json(paths.file("evidence"), evidence)
        context = {"question": request.question, "forecast_at": forecast_at,
                   "config": config.model_dump(mode="json")}
        if request.stage == "collect":
            save_collection(records, evidence, access, request.question, config, runtime.settings.data_dir, paths)
            return _collection_result(request, paths, config, context, mode, progress)
    elif request.stage == "route":
        _copy_parent(runtime, parent, paths, skip_report=mode == "llm")
        context = parent["context"]
        config = CLIConfig.model_validate(context["config"])
        if mode == "textbook":
            return _collection_result(request, paths, config, context, mode, progress)
        config.extraction.mode = "llm"
        context = {**context, "config": config.model_dump(mode="json")}
        records = read_json(paths.file("materials"))
        files = input_files(records, runtime.settings.data_dir)
    preparing = request.stage in {"prepare", "route"}
    if preparing:
        staged, references = stage_records(records, files, workspace)
        progress.phase("analyzing", "继续处理已采集材料，正在本地匹配信息、抽取数值并整理数据")
        try:
            analysis = run_component({"action": "cli-analyze", "records": staged,
                "question": context["question"], "reference_map": references, "output": str(paths.root),
                "config": config.model_dump(mode="json"), "output_layout": paths.organized},
                workspace, runtime.settings.data_dir,
                config.laptop.timeout_s, stream=True)
        except ServiceError as exc:
            analysis = fallback_analysis(context["question"], records, paths, exc.error.message)
        if mode == "deterministic":
            progress.phase("csv_ready", "确定性规则抽取完成，未调用LLM清洗")
            if not analysis.get("llm_cleaning"):
                analysis["llm_cleaning"] = {"status": "disabled", "model": "",
                                            "reason": "确定性规则路径不调用LLM清洗"}
        else:
            progress.phase("csv_ready", "匹配内容与初步数据已写入CSV，正在准备LLM清洗")
            analysis = clean_csv(context["question"], analysis, config, runtime.settings, paths, workspace,
                                 advisor=runtime.advisor, on_event=progress.phase)
        diagnostics = analysis.get("diagnostics")
        if diagnostics:
            progress.phase("data_diagnosis", diagnostics["message"])
            for item in diagnostics.get("rejection_reasons", [])[:3]:
                progress.phase("data_diagnosis", f"{item['message']}：{item['count']}条")
    else:
        _copy_parent(runtime, parent, paths)
        context = parent["context"]
        config = CLIConfig.model_validate(context["config"])
        analysis = read_json(paths.file("analysis"))
        if request.stage == "gan":
            config.gan.backend = request.framework
        context = {**context, "config": config.model_dump(mode="json")}
    payload = {"action": "cli-model", "output": str(paths.root), "output_layout": paths.organized,
               "config": config.model_dump(mode="json"),
               "forecast_at": context["forecast_at"], "stage": "gan" if request.stage == "gan" else "fit",
               "plan": preparing, "use_generated": request.use_generated}
    lock = DataPaths(runtime.settings.data_dir).heavy_lock()
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(lock, timeout=0):
            message = {"prepare": "正在检查真实数据量与建模门槛",
                       "route": "正在检查真实数据量与建模门槛",
                       "gan": "正在训练 WGAN；生成后将展示 t-SNE 并等待确认",
                       "model": "正在进行 XGBoost 建模与真实测试集评估"}[request.stage]
            progress.phase("modeling", message)
            modeling = run_component(payload, workspace, runtime.settings.data_dir,
                config.laptop.timeout_s if preparing else config.gan.timeout_s,
                stream=True)
    except Timeout:
        raise ServiceError("E_COMPUTE_BUSY", "另一项本地建模正在运行，请等待完成后重新提交。", 409) from None
    can_model = any(item["status"] in {"planned", "gan_review", "gan_unavailable", "exploratory_model"}
                    for item in modeling["series_reports"])
    if preparing and modeling.get("gan_gate"):
        progress.phase("gan_gate", modeling["gan_gate"]["reason"])
    stage = "choice" if preparing and can_model else (
        "gan_review" if request.stage == "gan" else "completed")
    if stage == "completed":
        progress.phase("summarizing", "正在调用 LLM 分析本轮数据的发展趋势" if config.llm == "api"
                       else "正在整理最终报告")
        evidence = read_json(paths.file("evidence"))
        summarize(context["question"], analysis, modeling, selected_evidence(paths, evidence), config,
                  runtime.settings, paths, advisor=runtime.advisor)
        advisor_status = read_json(paths.file("llm"))["status"]
        progress.phase("summary_result", "发展趋势分析已生成" if advisor_status == "completed"
                       else f"分析助手状态：{advisor_status}；数据与状态报告已保留")
    result = {"stage": stage, "run_id": f"{mode}/{paths.root.name}", "extraction": mode,
              "storage_layout": "output-v1", "context": context,
              "counts": analysis["counts"], "warning": analysis.get("warning"),
              "diagnostics": analysis.get("diagnostics"),
              "llm_cleaning": analysis.get("llm_cleaning"),
              "extraction_detail": analysis.get("extraction"),
              "data_quality": analysis.get("data_quality"),
              "can_model": can_model, "gan_available": modeling["needs_gan"],
              "gan_usable": any(item.get("gan", {}).get("status") == "accepted"
                                for item in modeling["series_reports"]),
              "access": read_json(paths.file("access")), "modeling": modeling,
              "llm": read_json(paths.file("llm")) if stage == "completed" else None,
              "report": read_text(paths.file("report")) if stage == "completed" else None}
    write_json(paths.file("config"), config.model_dump(mode="json"))
    write_json(paths.file("manifest"), {"storage_layout": "output-v1", "run_id": result["run_id"],
        "extraction": mode, "operation_id": request.operation_id, "parent_id": request.parent_id,
        "project_id": request.project_id, "stage": stage, "question": context["question"],
        "created_at": datetime.now().astimezone().isoformat(), "counts": result["counts"],
        "modeling": modeling, "llm_status": result["llm"]["status"] if result["llm"] else None})
    result["files"] = _files(paths)
    progress.phase("completed", {"choice": "信息整理完成，请选择是否训练 GAN",
        "gan_review": ("GAN 数据可供审查，请查看 t-SNE 并决定是否采用" if result["gan_usable"]
                       else "GAN 阶段处理结束，未取得可采用数据，请查看具体原因"),
        "completed": "本轮研究完成，结果与报告已保存"}[stage])
    return result
