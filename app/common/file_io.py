"""同类文件 I/O 的统一入口。

结构：文本解码 read_text → 原子文本写 write_text → 结构化 JSON 编解码。
调用关系：业务模块 → read_json/write_json → read_text/write_text → 标准库。
数据流：Path + 文本/JSON 值 ↔ UTF-8（LF）文件；读文本可按显式编码回退。
约束：写入使用同目录临时文件和原子替换；追加也由调用方先读后整体原子写。
"""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def read_text(path: Path, encodings: tuple[str, ...] = ("utf-8-sig", "gb18030")) -> str:
    """读取文本并按顺序解码；由结构化读入及提示词/记忆功能调用。"""
    data = path.read_bytes()
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码文件 {path.name}，请保存为 UTF-8 后重试。")


def write_text(path: Path, content: str) -> None:
    """以 UTF-8、LF 原子写入文本；由所有文本与 JSON 写入调用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def read_json(path: Path):
    """从统一文本入口解析 JSON；由站点策略与机器人会话状态调用。"""
    return json.loads(read_text(path))


def write_json(path: Path, value) -> None:
    """序列化 JSON 并原子落盘；由材料快照与机器人会话状态调用。"""
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_bytes(path: Path) -> bytes:
    """读取保真字节；由材料晋升与隔离副本构造调用。"""
    return path.read_bytes()


def write_bytes(path: Path, content: bytes) -> None:
    """原子保存保真字节；由采集适配器保存不可变材料调用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
