"""结构化文件与图表读写的统一入口；数据依赖按需导入。

结构：JSON / CSV / 数值权重数组读写 / save_figure。
调用：安装与工具包入口 → 对应格式的 I/O 函数；不调用功能层。
数据流：JSON ↔ dict；CSV ↔ DataFrame；数值数组 ↔ NPZ；Figure → PNG。
契约：读取返回对应对象，写入返回 None；路径由调用方提供。
"""

import csv
import io
from pathlib import Path

from app.common import file_io as platform_io


def read_json(path: Path) -> dict:
    """读取 UTF-8 JSON 对象；配置和后续结构化读取统一调用此处。"""
    result = platform_io.read_json(Path(path))
    if not isinstance(result, dict):
        raise ValueError("JSON 顶层必须是对象。")
    return result


def write_json(path: Path, value: dict) -> None:
    """写入可读的 UTF-8 JSON；本机安装结果由安装入口调用。"""
    platform_io.write_json(Path(path), value)


def read_csv(path: Path):
    """校验 CSV 记录宽度并保留原字符串；类型和缺失标记交由数据工具处理。"""
    import pandas as pd

    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as stream:
                reader = csv.reader(stream, strict=True)
                header = next(reader, [])
                names = [name.strip() for name in header]
                if not names or any(not name for name in names):
                    raise ValueError("CSV 为空或含空字段名，请补齐表头。")
                if len(set(names)) != len(names):
                    raise ValueError("CSV 存在重复字段名（含去除首尾空格后的重复），请先重命名。")
                for record in reader:
                    if record and len(record) != len(names):
                        raise ValueError(f"CSV 第 {reader.line_num} 行记录字段数为 {len(record)}，"
                                         f"与表头的 {len(names)} 列不一致，请检查分隔符或引号。")
                stream.seek(0)
                frame = pd.read_csv(stream, dtype=str, keep_default_na=False)
            frame.columns = names
            if frame.empty:
                raise ValueError("CSV 只有表头，没有可处理的数据行。")
            return frame
        except UnicodeDecodeError:
            continue
        except csv.Error as error:
            raise ValueError(f"CSV 引号或记录格式不合法：{error}") from error
    raise ValueError("CSV 编码无法识别，请另存为 UTF-8 CSV 后重试。")


def write_csv(path: Path, frame) -> None:
    """统一导出带 UTF-8 BOM 的 CSV，不写索引；输入路径保护由调用方负责。"""
    platform_io.write_frame(Path(path), frame)


def save_figure(path: Path, figure) -> None:
    """统一保存图表；调用方负责关闭 Figure，路径不在此生成。"""
    platform_io.save_figure(Path(path), figure)


def write_arrays(path: Path, arrays: dict) -> None:
    """保存纯数值模型数组；两个后端共用，不序列化 Python 对象。"""
    import numpy as np

    stream = io.BytesIO()
    np.savez_compressed(stream, **arrays)
    platform_io.write_bytes(Path(path), stream.getvalue())


def read_arrays(path: Path) -> dict:
    """读取不含 pickle 的模型数组；模型结构由元数据描述。"""
    import numpy as np

    with np.load(io.BytesIO(platform_io.read_bytes(Path(path))), allow_pickle=False) as archive:
        return {name: archive[name] for name in archive.files}


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        temporary_path = Path(folder) / "sample.json"
        write_json(temporary_path, {"status": "ok"})
        assert read_json(temporary_path) == {"status": "ok"}
    print("JSON I/O self-check passed")
