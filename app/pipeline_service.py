"""网页完整管线显式聚合层；结构：validate → execute → prepare/gan/model。

调用：API校验；持久Worker → 本入口 → CLI采集/离线桥接/最终助手。
数据流：网址+需求 → 整理及门槛 → 人工选择GAN → t-SNE审查 → 人工采用 → 建模/建议。
每次领取使用独立目录，父结果只读；API不等待训练、不接收任意路径，不自动重复未知付费结果。
"""
from datetime import datetime

from filelock import FileLock, Timeout

from app.common.component import run_component
from app.common.evidence_files import stage_records
from app.common.file_io import read_bytes, read_json, read_text, write_bytes, write_json
from app.common.paths import CLI_CONFIG, DataPaths, PipelinePaths
from app.contracts.pipeline import CLIConfig
from app.contracts.pipeline_web import PipelineRequest
from app.core.errors import ServiceError
from app.pipeline_cli import collect, fallback_analysis, selected_evidence, summarize


def parent_result(runtime, request):
    """只引用同项目完成的管线结果，禁止跨类型、跨阶段或跨项目依赖。"""
    operation = runtime.operations.get(request.parent_id, request.project_id)
    if operation["kind"] != "pipeline" or operation["state"] != "done":
        raise ServiceError("E_PIPELINE_PARENT", "上一步尚未完成或任务类型不符。", 409)
    parent = operation["result"]
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
    if request.stage != "prepare":
        parent_result(runtime, request)


def _files(paths):
    """列出本轮可下载产物；只含目录内登记的普通文件，不接受浏览器路径。"""
    files = {}
    series = sorted(paths.series_parent().glob("*")) if paths.series_parent().is_dir() else []
    for group in [paths, *[PipelinePaths(path) for path in series if path.is_dir() and not path.is_symlink()]]:
        for file in group.registered_files():
            if file.is_file() and file.resolve().is_relative_to(paths.root.resolve()):
                reference = file.relative_to(paths.root).as_posix()
                files[reference] = reference
    return files


def _copy_parent(runtime, parent, paths):
    """仅复制父操作登记文件到本令牌目录；旧Worker和其他决策不会互相覆盖。"""
    source = DataPaths(runtime.settings.data_dir).pipeline(parent["run_id"])
    for reference in parent["files"]:
        origin = (source.root / reference).resolve()
        destination = (paths.root / reference).resolve()
        if not origin.is_relative_to(source.root.resolve()) or not destination.is_relative_to(paths.root.resolve()):
            raise ServiceError("E_ARTIFACT_PATH", "产物引用越界。", 409)
        write_bytes(destination, read_bytes(origin))


def execute(runtime, claim):
    """Worker执行一个阶段并返回页面所需真实状态；完整模型摘要只在最后传给助手。"""
    request = PipelineRequest.model_validate(claim.request)
    if claim.attempts > 1 and request.stage == "model":
        raise ServiceError("E_PIPELINE_OUTCOME_UNKNOWN", "建模/建议执行曾中断，请核对产物后重新提交新操作。", 409)
    paths = DataPaths(runtime.settings.data_dir).pipeline(claim.token.replace("-", ""))
    workspace = DataPaths(runtime.settings.data_dir).research_workspace(claim.token.replace("-", ""))
    if request.stage == "prepare":
        config = CLIConfig.model_validate(read_json(CLI_CONFIG))
        config.llm, config.retrieval = request.llm, request.retrieval
        config.gan.mode = "auto"
        forecast_at = datetime.now().astimezone().isoformat()
        records, files, evidence, access = collect(request.urls, config, runtime.settings, adapter=runtime.adapter)
        write_json(paths.file("access"), access)
        write_json(paths.file("materials"), records)
        write_json(paths.file("evidence"), evidence)
        staged, references = stage_records(records, files, workspace)
        try:
            analysis = run_component({"action": "cli-analyze", "records": staged,
                "question": request.question, "reference_map": references, "output": str(paths.root),
                "config": config.model_dump(mode="json")}, workspace, runtime.settings.data_dir,
                config.laptop.timeout_s, stream=True)
        except ServiceError as exc:
            analysis = fallback_analysis(request.question, records, paths, exc.error.message)
        context = {"question": request.question, "forecast_at": forecast_at,
                   "config": config.model_dump(mode="json")}
    else:
        parent = parent_result(runtime, request)
        _copy_parent(runtime, parent, paths)
        context = parent["context"]
        config = CLIConfig.model_validate(context["config"])
        analysis = read_json(paths.file("analysis"))
        if request.stage == "gan":
            config.gan.backend = request.framework
        context = {**context, "config": config.model_dump(mode="json")}
    payload = {"action": "cli-model", "output": str(paths.root), "config": config.model_dump(mode="json"),
               "forecast_at": context["forecast_at"], "stage": "gan" if request.stage == "gan" else "fit",
               "plan": request.stage == "prepare", "use_generated": request.use_generated}
    lock = DataPaths(runtime.settings.data_dir).heavy_lock()
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(lock, timeout=0):
            modeling = run_component(payload, workspace, runtime.settings.data_dir,
                config.laptop.timeout_s if request.stage == "prepare" else config.gan.timeout_s,
                stream=True)
    except Timeout:
        raise ServiceError("E_COMPUTE_BUSY", "另一项本地建模正在运行，请等待完成后重新提交。", 409) from None
    can_model = any(item["status"] in {"planned", "gan_review", "gan_unavailable", "exploratory_model"}
                    for item in modeling["series_reports"])
    stage = "choice" if request.stage == "prepare" and can_model else (
        "gan_review" if request.stage == "gan" else "completed")
    if stage == "completed":
        evidence = read_json(paths.file("evidence"))
        summarize(context["question"], analysis, modeling, selected_evidence(paths, evidence), config,
                  runtime.settings, paths, advisor=runtime.advisor)
    result = {"stage": stage, "run_id": paths.root.name, "context": context,
              "counts": analysis["counts"], "warning": analysis.get("warning"),
              "can_model": can_model, "gan_available": modeling["needs_gan"],
              "gan_usable": any(item.get("gan", {}).get("status") == "accepted"
                                for item in modeling["series_reports"]),
              "access": read_json(paths.file("access")), "modeling": modeling,
              "llm": read_json(paths.file("llm")) if stage == "completed" else None,
              "report": read_text(paths.file("report")) if stage == "completed" else None}
    write_json(paths.file("config"), config.model_dump(mode="json"))
    result["files"] = _files(paths)
    return result
