"""真实爬虫显式适配/聚合层，不依赖业务服务。

结构：IntegratedCrawler.probe/crawl_one → run_component → _promote → recover_metadata。
调用：API/Worker → 本适配器 → 隔离旧爬虫；数据流：合同请求 → 隔离抓取 → 内容寻址材料。
返回：Profile[] / CrawlResult；所有文件引用均限定在服务 data 根目录，不覆盖历史内容。
"""

import hashlib
from pathlib import Path
from uuid import uuid4

from app.common.component import run_component
from app.common.file_io import read_bytes, write_bytes
from app.common.paths import DataPaths
from app.contracts.crawler import CrawlResult, Profile
from app.core.errors import ServiceError
from app.crawler.html_metadata import recover_metadata


class IntegratedCrawler:
    """为单个服务根目录绑定真实组件；runtime 负责配置注入。"""

    def __init__(self, data_root: Path, timeout: float = 600):
        """保存纯配置，无读写/联网；由 runtime 调用。"""
        self.paths, self.timeout = DataPaths(data_root), timeout

    def probe(self, request, policies):
        """逐批在隔离进程探测，所有请求经过服务网络闸门；由 API 调用。"""
        workspace = self.paths.crawler_workspace(uuid4().hex)
        result = run_component({"action": "probe", "request": request.model_dump(mode="json"),
                                "policies": [p.model_dump() for p in policies]},
                               workspace, self.paths.root, max(self.timeout, 30 * len(request.urls)))
        profiles = []
        for item in result["profiles"]:
            item["text_chars"] = item.get("text_chars") or 0
            if isinstance(item.get("error"), str):
                item["error"] = {"code": "E_PROBE", "message": item["error"], "retryable": False}
            profiles.append(Profile.model_validate(item))
        return profiles

    def crawl_one(self, request, url, context):
        """执行单 URL 并晋升不可变文件；由持租约 Worker 调用。"""
        if request.allow_render or request.allow_tls_impersonate:
            raise ServiceError("E_TOOL_UNAVAILABLE", "服务暂仅开放静态网页与文本 PDF；渲染档需另行适配。", 422)
        workspace = self.paths.crawler_workspace(uuid4().hex)
        payload = request.model_dump(mode="json")
        payload["urls"] = [url]
        result = run_component({"action": "crawl", "request": payload,
                                "policies": [context.policy.model_dump()],
                                "delay": request.per_host_delay_s},
                               workspace, context.data_dir, self.timeout)
        if len(result.get("results", [])) != 1:
            raise ServiceError("E_ADAPTER_CONTRACT", "组件未返回唯一材料，请检查适配器。", 500)
        record = result["results"][0]
        record.setdefault("meta", {})["code_version"] = context.code_version
        if record.get("pdf") and not record["pdf"].get("text_path"):
            record["pdf"] = None
        self._promote(record, workspace)
        return CrawlResult.model_validate(record)

    def _promote(self, record: dict, workspace: Path) -> None:
        """只晋升已登记文件引用，验证边界并按内容哈希保存；由 crawl_one 调用。"""
        def promote(reference, category):
            """将隔离工作区文件转换成服务相对引用；仅本晋升过程调用。"""
            if not reference:
                return None
            source = Path(reference)
            source = source if source.is_absolute() else workspace / source
            source = source.resolve()
            if not source.is_relative_to((workspace / "data").resolve()) or not source.is_file():
                raise ServiceError("E_ADAPTER_PATH", "爬虫产物引用越界或缺失。", 500)
            content = read_bytes(source)
            if category == "raw/html" and record.get("status") == "ok":
                fields, audit = recover_metadata(content, record.get("encoding"),
                                                 record.get("source") or {}, record.get("title"))
                if "published_at" in fields:
                    record.setdefault("source", {})["published_at"] = fields["published_at"]
                if "title" in fields:
                    record["title"] = fields["title"]
                record.setdefault("meta", {})["source_extraction"] = audit
            digest = hashlib.sha256(content).hexdigest()
            target = self.paths.artifact_path(category, record["record_id"], digest, source.suffix)
            if not target.resolve().is_relative_to(self.paths.root.resolve()):
                raise ServiceError("E_ADAPTER_PATH", "目标产物目录越界。", 500)
            write_bytes(target, content)
            return "data/" + target.relative_to(self.paths.root).as_posix()

        storage = record.setdefault("storage", {})
        storage["raw_path"] = promote(storage.get("raw_path"), "raw/html" if record["verdict"] != "pdf"
                                      else "raw/files")
        storage["text_path"] = promote(storage.get("text_path"), "normalized/text")
        storage["tables_dir"] = None
        record["paragraph_file"] = promote(record.get("paragraph_file"), "normalized/para")
        if record.get("pdf"):
            record["pdf"]["text_path"] = promote(record["pdf"].get("text_path"), "normalized/pdf")
        for table in record.get("tables", []):
            for name in ("html_path", "json_path", "csv_path"):
                table[name] = promote(table.get(name), "normalized/tables")
