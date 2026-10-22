"""同类文件 I/O 的统一入口。

结构：文本解码 read_text → 原子文本写 write_text → 结构化 JSON 编解码。
调用关系：业务模块 → read_json/write_json → read_text/write_text → 标准库。
数据流：Path + 文本/JSON 值 ↔ UTF-8（LF）文件；读文本可按显式编码回退。
约束：写入使用同目录临时文件和原子替换；追加也由调用方先读后整体原子写。
"""

import csv
import io
import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile


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
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_csv_rows(path: Path) -> list[dict]:
    """读取带 BOM 的审核 CSV；由 CLI 审核及离线分析调用。"""
    return list(csv.DictReader(io.StringIO(read_text(path).lstrip("\ufeff"))))


def write_csv_rows(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    """统一写候选与审计 CSV，嵌套字段存 JSON；由管线聚合入口调用。"""
    fields = fields or list(dict.fromkeys(key for row in rows for key in row))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(
            value, (list, dict)) else value for key, value in row.items()})
    write_text(path, "\ufeff" + stream.getvalue())


def read_frame(path: Path, max_rows: int = 10000):
    """有界读取原字段表，保留字符串供缺失诊断，不隐式填值。"""
    import pandas as pd

    frame = pd.read_csv(io.StringIO(read_text(path)), dtype=str, keep_default_na=False,
                        nrows=max_rows + 1)
    if len(frame) > max_rows:
        raise ValueError(f"输入超过 {max_rows} 行，请先缩小训练数据。")
    return frame


def write_frame(path: Path, frame) -> None:
    """统一原子导出 DataFrame CSV，兼容 Excel BOM。"""
    write_text(path, "\ufeff" + frame.to_csv(index=False))


def write_zip(path: Path, entries) -> None:
    """按调用方提供的受限相对名称打包字节，原子保存，不解压任何外部压缩包。"""
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in entries:
            candidate = Path(name)
            if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
                raise ValueError("材料包文件名必须是包内相对路径。")
            archive.writestr(candidate.as_posix(), content)
    write_bytes(path, stream.getvalue())


def save_figure(path: Path, figure) -> None:
    """图表统一经字节入口原子保存；图形创建和关闭由绘图模块负责。"""
    stream = io.BytesIO()
    figure.savefig(stream, format="png", dpi=140, bbox_inches="tight")
    write_bytes(path, stream.getvalue())


def reset_pipeline_outputs(paths) -> None:
    """只删除地址簿登记的上轮产物，保留用户其他文件；拒绝目录联接和链接越界。"""
    root = paths.root
    if root.is_symlink() or root.is_junction():
        raise ValueError("output 不能使用符号链接或目录联接。")
    root.mkdir(parents=True, exist_ok=True)
    collections = [paths]
    parent = paths.series_parent()
    if parent.exists():
        if parent.is_symlink() or parent.is_junction():
            raise ValueError("output/series 不能使用链接。")
        collections.extend(paths.series(child.name) for child in parent.iterdir()
                           if child.is_dir() and re.fullmatch(r"[0-9a-f]{12}", child.name))
    targets = []
    for item in collections:
        for target in item.registered_files():
            current = target
            while current != root:
                if current.is_symlink() or current.is_junction():
                    raise ValueError("登记产物中存在链接，请移除链接后重试。")
                current = current.parent
            if not target.resolve().is_relative_to(root.resolve()):
                raise ValueError("登记产物越过 output，停止覆盖。")
            if target.is_file():
                targets.append(target)
    # 所有目标先检查完成，再逐文件清理；从不递归删除整个 output。
    for target in targets:
        target.unlink()


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
