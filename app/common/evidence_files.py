"""隔离研究输入准备，共用基础设施，不调用业务模块。

结构：stage_records；调用：研究适配器/CLI → 本模块 → 地址簿/统一 I/O。
数据流：已验证的材料引用和文件 → 短文件名副本 → 记录副本及反向引用表。
"""

import hashlib
from copy import deepcopy

from app.common.file_io import read_bytes, write_bytes
from app.common.paths import DataPaths


def stage_records(records, files, workspace):
    """构造与旧研究读取器兼容的输入副本，避免改写原材料及 Windows 长路径。"""
    mapped = {}
    paths = DataPaths(workspace)
    for reference, source in files:
        category = "para" if source.suffix == ".jsonl" else "text"
        new_reference = paths.research_input_reference(category,
            hashlib.sha256(reference.encode()).hexdigest()[:24], source.suffix)
        mapped[reference] = new_reference
        target = DataPaths(workspace / "data").normalized_reference(new_reference)
        if not target.is_relative_to((workspace / "data/normalized").resolve()):
            raise ValueError("研究输入路径越界。")
        write_bytes(target, read_bytes(source))
    copied = deepcopy(records)
    for record in copied:
        storage = record["storage"]
        storage["text_path"] = mapped.get(storage.get("text_path"))
        record["paragraph_file"] = mapped.get(record.get("paragraph_file"))
        if record.get("pdf"):
            record["pdf"]["text_path"] = mapped[record["pdf"]["text_path"]]
    return copied, {value: key for key, value in mapped.items()}
