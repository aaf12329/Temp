"""统一产物路径解析；文件名只取自common.paths，模块本身不执行I/O。

① 纯判定：artifact_paths；调用树：入口/训练/数据聚合 → artifact_paths → pathlib。
数据流：调用方输出目录 + 地址簿文件名映射 → dict[str, Path]。
契约：不创建目录、不探测文件、不调用入口层或功能层；可注入临时目录。
"""

from pathlib import Path
from typing import Mapping


def artifact_paths(directory, filenames: Mapping[str, str]) -> dict[str, Path]:
    """解析地址簿的文件集合；由各入口统一传入输出目录。"""
    directory = Path(directory).expanduser().resolve()
    return {name: directory / filename for name, filename in filenames.items()}


if __name__ == "__main__":
    from common.paths import TRAINING_ARTIFACT_NAMES, RESULTS_DIRECTORY

    assert artifact_paths(RESULTS_DIRECTORY, TRAINING_ARTIFACT_NAMES)["model"].name == "model.npz"
    print("Artifact paths self-check passed; no files created.")
