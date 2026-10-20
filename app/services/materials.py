"""功能层（依赖共用层，不横向导入功能模块）。
结构导航：
├─ Materials
│  ├─ __init__
│  ├─ get
│  ├─ text
│  ├─ paragraphs
│  ├─ table
│  ├─ _file
调用关系：
├─ get → self.db.session
├─ text → self.get, self._file
├─ paragraphs → self._file, self.get
├─ table → self.get, self._file
├─ _file → self.storage.resolve_normalized。
数据流：record_id → 材料元数据 → 已登记引用 → 受限文件路径。
"""

from app.common.storage import Storage
from app.contracts.crawler import CrawlResult
from app.core.errors import ServiceError
from app.db.models import MaterialRow
from app.db.session import Database


class Materials:
    def __init__(self, db: Database, storage: Storage):
        """绑定当前对象的配置、依赖和实例状态；由所属入口构造流程调用。"""
        self.db, self.storage = db, storage

    def get(self, record_id: str) -> CrawlResult:
        """按主键读取所属功能的固定返回契约；由对应业务入口调用。"""
        with self.db.session() as session:
            row = session.get(MaterialRow, record_id)
            if row is None:
                raise ServiceError("E_MATERIAL_NOT_FOUND", "未找到材料记录。", 404)
            return CrawlResult.model_validate(row.payload)

    def text(self, record_id: str):
        """解析材料正文引用并校验文件边界；由正文路由调用。"""
        material = self.get(record_id)
        reference = material.storage.get("text_path")
        if not reference and material.pdf:
            reference = material.pdf.text_path
        return self._file(reference)

    def paragraphs(self, record_id: str):
        """解析段落引用并校验文件边界；由段落路由调用。"""
        return self._file(self.get(record_id).paragraph_file)

    def table(self, record_id: str, index: int, format: str):
        """选择结构化表格引用并校验边界；由表格路由调用。"""
        material = self.get(record_id)
        for table in material.tables:
            if table.index == index:
                return self._file(table.json_path if format == "json" else table.csv_path)
        raise ServiceError("E_TABLE_NOT_FOUND", "未找到指定表格。", 404)

    def _file(self, reference):
        """把存在的产物引用交给共用存储校验；由材料功能方法调用。"""
        if not reference:
            raise ServiceError("E_ARTIFACT_NOT_FOUND", "该材料没有对应的规范化产物。", 404)
        return self.storage.resolve_normalized(reference)
