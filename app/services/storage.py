"""显式兼容聚合模块：保留旧 Storage 导入位置，只重导出共用层。

结构/调用：外部旧入口 → 本模块 → app.common.storage.Storage。
数据流与返回契约保持不变；新功能模块直接依赖 common。
"""

from app.common.storage import Storage

__all__ = ["Storage"]
