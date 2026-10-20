"""同类文件 I/O 的统一入口。

结构：文本解码 read_text → 原子文本写 write_text → 结构化 JSON 编解码。
调用关系：业务模块 → read_json/write_json → safe_path + read_text/write_text → 标准库。
数据流：Path + 文本/JSON 值 ↔ UTF-8（LF）文件；读文本可按显式编码回退。
约束：写入使用同目录临时文件和原子替换；追加也由调用方先读后整体原子写。

函数结构树（AST 核对）：
├─ safe_path
├─ read_text
├─ write_text
├─ read_json
├─ write_json
项目内调用关系：
├─ safe_path → BotError
├─ read_json → read_text, safe_path
├─ write_json → write_text, safe_path
"""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from advisor_bot.shared.errors import BotError


def safe_path(path: Path, root: Path) -> Path:
    """验证 JSON 文件未越界或指向敏感文件；由结构化读写入口调用。"""
    resolved = path.resolve()
    if (
        not resolved.is_relative_to(root.resolve())
        or resolved.name.lower() in {".env", "id_rsa", "id_ed25519"}
        or resolved.suffix.lower() in {".pem", ".key"}
    ):
        raise BotError("E_STORAGE_PATH", "存储文件越界或指向敏感文件，请检查数据目录与符号链接。")
    return resolved


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


def read_json(path: Path, *, root: Path | None = None):
    """从统一文本入口解析 JSON；由站点策略与机器人会话状态调用。"""
    return json.loads(read_text(safe_path(path, root) if root else path))


def write_json(path: Path, value, *, root: Path | None = None) -> None:
    """序列化 JSON 并原子落盘；由材料快照与机器人会话状态调用。"""
    write_text(
        safe_path(path, root) if root else path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )
