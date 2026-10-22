"""完整管线HTTP与阶段隔离验收：假来源/生成器，真实队列、时间切分、XGB和图表。

结构：数据不足端到端 → 人工阶段/跨项目/产物边界；pytest调用，无真实网站或付费模型。
"""
import numpy as np
import pandas as pd

from app.common.file_io import read_json, write_frame, write_json
from app.common.paths import DataPaths, PipelinePaths
from app.operation_worker import run_operation
from tests.test_pipeline import legacy_imports as legacy_imports


def submit_and_run(client, payload):
    """通过实际HTTP提交、持久领取执行，再查询完成状态。"""
    response = client.post("/v1/pipeline/jobs", json=payload)
    assert response.status_code == 202, response.text
    assert run_operation(client.app.state.runtime, payload["operation_id"])
    operation = client.get(f"/v1/operations/{payload['operation_id']}?project_id=local").json()
    assert operation["state"] == "done", operation
    return operation["result"]


def test_data_only_real_bridge_and_artifact_download(client):
    """假采集适配器+真实离线桥接，数据不足直接报告且不启动GAN/XGB。"""
    result = submit_and_run(client, {"operation_id": "data-only", "project_id": "local", "stage": "prepare",
        "urls": ["https://example.test/a"], "question": "示例数量", "retrieval": "keywords", "llm": "off"})
    assert result["stage"] == "completed" and not result["can_model"]
    assert result["llm"]["status"] == "disabled"
    assert "report.md" in result["files"]
    response = client.get("/v1/pipeline/data-only/files/report.md?project_id=local")
    assert response.status_code == 200
    assert "探索" in response.text
    assert client.get("/v1/pipeline/data-only/files/.env?project_id=local").status_code == 404
    assert client.get("/v1/pipeline/data-only/files/report.md?project_id=other").status_code == 404
    denied = client.post("/v1/pipeline/jobs", json={"operation_id": "no-gate", "stage": "gan", "parent_id": "data-only"})
    assert denied.status_code == 409


def test_manual_gan_stages_and_immutable_parent(client, monkeypatch, legacy_imports):
    """真实时间切分/建模；生成器替身只验证人工确认、5倍与父产物隔离。"""
    from app import pipeline_service
    from app.pipeline import training, workflow

    calls = []
    rows = [{"series_id": "synthetic", "scope": "SYNTHETIC ONLY", "period": 1900 + i,
             "value": float(100 + i * 2 + np.sin(i) * 5), "known_at": f"{1900 + i}-12-31"} for i in range(100)]

    def offline(payload, *args, **kwargs):
        """仅将输入分析替换为合成年表，模型阶段执行真实生产代码。"""
        if payload["action"] == "cli-analyze":
            value = {"counts": {"documents": 1, "selected_passages": 0, "candidate_facts": 100, "series_rows": 100},
                     "series": rows, "facts": [], "warning": "SYNTHETIC TEST ONLY"}
            paths = PipelinePaths(__import__("pathlib").Path(payload["output"]),
                                  organized=payload.get("output_layout", False))
            write_json(paths.file("analysis"), value)
            from app.common.file_io import write_csv_rows

            write_csv_rows(paths.file("information"), [], ["record_id", "text"])
            return value
        return workflow.model(payload)

    def generate(frame, config, paths, threads):
        """合成生成器替身，不声称GAN质量；保留5倍及用户尚未采用的标记。"""
        calls.append(len(frame))
        generated = pd.concat([frame] * 5, ignore_index=True)
        write_frame(paths.file("generated"), generated.assign(is_synthetic=True, accepted_for_model=False))
        return generated, {"status": "accepted", "synthetic_rows": len(generated),
                           "requested_synthetic_rows": len(frame) * 5, "reason": "fixture only"}

    monkeypatch.setattr(pipeline_service, "run_component", offline)
    monkeypatch.setattr(training, "augment", generate)
    first = submit_and_run(client, {"operation_id": "ready", "stage": "prepare", "urls": ["https://example.test/a"],
                                   "question": "合成流程验收", "llm": "off"})
    assert first["stage"] == "choice" and first["gan_available"] and not calls
    rejected = client.post("/v1/pipeline/jobs", json={"operation_id": "premature", "stage": "model", "parent_id": "ready", "use_generated": True})
    assert rejected.status_code == 409
    review = submit_and_run(client, {"operation_id": "gan", "stage": "gan", "parent_id": "ready"})
    assert review["stage"] == "gan_review" and review["gan_usable"] and len(calls) == 1
    generated = review["modeling"]["series_reports"][0]["gan"]
    assert generated["synthetic_rows"] == calls[0] * 5
    completed = submit_and_run(client, {"operation_id": "fit", "stage": "model", "parent_id": "gan", "use_generated": True})
    assert len(calls) == 1  # 用户采用后绝不重新生成另一批数据。
    assert completed["stage"] == "completed"
    assert completed["modeling"]["series_reports"][0]["test"]["selected"]["n"] == 4
    assert any(name.endswith("shap.png") for name in completed["files"])
    source = DataPaths(client.app.state.runtime.settings.data_dir).pipeline(review["run_id"], output=True)
    assert not any(source.root.rglob("shap.png"))
    parent_review = read_json(source.series(__import__("hashlib").sha256(b"synthetic").hexdigest()[:12]).file("gan_review"))
    assert not parent_review["gan"].get("user_accepted", False)
    assert client.post("/v1/pipeline/jobs", json={"operation_id": "wrong-project", "project_id": "other", "stage": "model", "parent_id": "gan"}).status_code == 404


def test_deterministic_mode_skips_cleaning_and_layers_run_output(client, monkeypatch, legacy_imports):
    """确定性路径不调用清洗入口，任务目录按 deterministic 分层。"""
    from pathlib import Path

    from app import pipeline_service
    from app.common.file_io import write_csv_rows
    from app.pipeline import workflow

    def offline(payload, *args, **kwargs):
        """只替换分析产物，模型阶段仍走生产代码。"""
        if payload["action"] == "cli-analyze":
            value = {"counts": {"documents": 1, "selected_passages": 0, "candidate_facts": 0,
                                "series_rows": 0}, "series": [], "facts": [],
                     "warning": "SYNTHETIC TEST ONLY"}
            paths = PipelinePaths(Path(payload["output"]), organized=payload.get("output_layout", False))
            write_json(paths.file("analysis"), value)
            write_csv_rows(paths.file("information"), [], ["record_id", "text"])
            return value
        return workflow.model(payload)

    monkeypatch.setattr(pipeline_service, "run_component", offline)
    result = submit_and_run(client, {"operation_id": "det-mode", "stage": "prepare",
        "urls": ["https://example.test/a"], "question": "示例数量", "retrieval": "keywords",
        "llm": "off", "extraction": "deterministic"})
    assert result["extraction"] == "deterministic"
    assert result["run_id"].startswith("deterministic/")
    assert result["llm_cleaning"]["status"] == "disabled"
