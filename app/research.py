"""研究模块显式适配/聚合入口。

结构：IntegratedResearch.execute → 隔离输入副本 → research_api.analyze。
调用：操作 Worker → 本入口 → run_component；数据流：受信材料 → 离线分析 → CSV/JSON 文件名清单。
只返回计数、趋势状态与下载名；原始分析含证据，可通过鉴权下载，不泄露本机路径。
"""


from app.common.component import run_component
from app.common.evidence_files import stage_records
from app.common.file_io import write_json
from app.common.paths import DataPaths


class IntegratedResearch:
    """负责把已有材料映射到旧研究 API 的隔离目录。"""

    def __init__(self, settings):
        """绑定服务配置；由 runtime 构造。"""
        self.settings, self.paths = settings, DataPaths(settings.data_dir)

    def execute(self, request: dict, records: list[dict], files: list[tuple], run_id: str) -> dict:
        """执行一次离线分析并返回稳定结果；由操作 Worker 调用。"""
        workspace = self.paths.research_workspace(run_id)
        records, references = stage_records(records, files, workspace)
        config_file = self.paths.bridge_file(workspace, "config")
        write_json(config_file, request["config"])
        output = self.paths.operation_directory(run_id)
        result = run_component({"action": "analyze", "request": {
            "records": records, "mode": request["mode"], "config_path": str(config_file),
            "output_dir": str(output), "reference_map": references}},
            workspace, self.settings.data_dir,
            self.settings.bridge_timeout_s)
        return {"counts": result["counts"], "trend_status": result["trend_status"],
                "run_id": run_id, "files": list(result["paths"]),
                "human_review_required": True, "mode": request["mode"]}
