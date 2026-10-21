# -*- coding: utf-8 -*-
"""I/O 单一入口 —— 文本一个口子、结构化数据一个口子，不开第三处。

《工程架构规范》§04：编码 / 换行 / 原子写这些问题只解决一次；
要加日志或审计，只改这一个文件。

============================== 函数结构树 ==============================
① 文本口子
    read_text(path)            读文本（自动回退 utf-8 / utf-8-sig / gbk）
    write_text(path, text)     原子写文本（先写 .tmp 再替换）
② 字节口子（原始快照专用）
    read_bytes(path)           读原始字节
    write_bytes(path, data)    原子写原始字节（快照用它，字节保真）
③ 结构化数据口子
    read_json(path)            读 JSON
    write_json(path, data)     原子写 JSON（ensure_ascii=False，中文可读）
    write_csv(path, rows, fields)
                               原子写 CSV（utf-8-sig，Excel 直开）
④ 清单读取
    read_url_lines(path)       一行一条 URL，跳过空行与 # 注释
    load_seed_urls(limit)      读地址簿登记的全部种子清单（爬虫入口用）
⑤ 文件名与指纹（由内容唯一决定，重跑不会越堆越多）
    make_file_name(url, suffix)
                               由 URL 推出快照文件名：<主机>_<URL哈希10><后缀>
    sha256_of(data)            内容指纹（字节 / 文本都行）
⑥ 目录准备
    ensure_parent(path)        只建父目录，不建文件

============================== 调用关系总览 ==============================
read_text / write_text / read_bytes / write_bytes / read_json / write_json /
write_csv / read_url_lines
        └─▶ ensure_parent        （需要落盘时才建目录）
load_seed_urls ──▶ read_url_lines ──▶ Path_Store.SEED_FILES

=============================== 说明 ===============================
本模块**不 import 功能层**，只读地址簿（Path_Store）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from Path_Store import Path_Store as ps
except ImportError:                     # 直接运行本文件（人工试跑）时，把 src 加进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from Path_Store import Path_Store as ps

# 读取候选编码：Windows 上的旧文件常是 gbk，新文件是 utf-8
READ_ENCODINGS = ("utf-8", "utf-8-sig", "gbk")


def ensure_parent(path: str | Path) -> Path:
    """确保目标文件的父目录存在（只建目录，不动文件）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def read_text(path: str | Path) -> str:
    """读文本；依次回退 utf-8 / utf-8-sig / gbk，全失败则抛出原始异常。"""
    target = Path(path)
    last_error: Exception | None = None
    for encoding in READ_ENCODINGS:
        try:
            return target.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def write_text(path: str | Path, text: str) -> None:
    """原子写文本：先写同目录 .tmp 文件，再一次性替换，避免半截文件。"""
    target = ensure_parent(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)


def read_bytes(path: str | Path) -> bytes:
    """读原始字节（原始快照用）。"""
    return Path(path).read_bytes()


def write_bytes(path: str | Path, data: bytes) -> None:
    """原子写原始字节：先写 .tmp 再替换，避免半截文件；快照用它能保真。"""
    target = ensure_parent(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def read_json(path: str | Path) -> Any:
    """读 JSON，返回解析后的对象。"""
    return json.loads(read_text(path))


def write_json(path: str | Path, data: Any) -> None:
    """原子写 JSON；中文不转义，便于人工核对。"""
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def write_csv(path: str | Path, rows: Iterable[dict[str, Any]],
              fields: Sequence[str]) -> None:
    """原子写 CSV：utf-8-sig 带 BOM，Excel 双击直开不乱码。

    必须自己拼 BOM —— `write_text` 固定用 utf-8，不拼的话中文在 Excel 里是乱码
    （真踩过：合并写 CSV 时漏了 BOM，子代理的字节断言当场抓到）。
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fields), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fields})
    write_text(path, "\ufeff" + buffer.getvalue())


def read_url_lines(path: str | Path) -> list[str]:
    """读 URL 清单：一行一条，跳过空行与 `#` 注释行，保持原顺序。"""
    urls: list[str] = []
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def load_seed_urls(limit: int = 0) -> list[str]:
    """读地址簿里登记的全部种子清单；`limit>0` 时只取前 N 条。"""
    urls: list[str] = []
    for seed in ps.SEED_FILES:
        urls.extend(read_url_lines(seed))
    return urls[:limit] if limit and limit > 0 else urls


def sha256_of(data: str | bytes) -> str:
    """内容指纹：同一内容永远得到同一串（去重、快照命名都靠它）。"""
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()


def make_file_name(url: str, suffix: str = ".html") -> str:
    """由 URL 推出快照文件名：**只由 URL 决定** —— <主机>_<URL哈希10><后缀>。

    同一个 URL 永远得到同一个文件名，重抓即**覆盖**，不会越跑堆越多份。
    """
    from urllib.parse import urlsplit

    target = url if "://" in url else "https://" + url
    parts = urlsplit(target)
    raw_host = parts.netloc or "unknown"
    host = "".join(ch if (ch.isalnum() or ch in ".-") else "-" for ch in raw_host)
    return f"{host}_{hashlib.sha256(target.encode('utf-8')).hexdigest()[:10]}{suffix}"
