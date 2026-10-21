# -*- coding: utf-8 -*-
"""PDF 解析（纯标准库）—— 把 PDF 的文本层抽出来，并保留**页码映射**。

《爬虫标准API接口.md》v1.2 §4.4：
统计年鉴 / 统计公报大量是 PDF，不解析等于把最重要的数据源变成黑盒。
下游引用一个数字时要能说「第 7 页」，所以必须给 `page_map`。

🔴 诚实边界：本模块只处理**有文本层**的 PDF。扫描件（纯图片）抽不出字，
   返回 `E_PDF_NO_TEXT`，**绝不假装解析成功**。OCR 不在本期范围。

================================ 结构树 =================================
① PDF 结构
    iter_objects(data)         拆出所有 "N 0 obj … endobj"
    decompress_streams(data)   把所有 FlateDecode 流解压出来
② 字体映射
    parse_cmaps(streams)       收集 ToUnicode 的 bfchar/bfrange → CID→Unicode
③ 文本抽取
    extract_text(content, cmap) 处理 Tj / TJ 文本操作符
④ 汇总
    parse(data) -> {pages, text, page_map, error}
⑤ 自测
    _self_test()               零网络（用手工构造的假流）

================================ 调用关系 ================================
api.py ──▶ parse() ──▶ store 落盘 data/normalized/pdf/*.txt
"""

from __future__ import annotations

import re
import sys
import zlib
from pathlib import Path
from typing import Any

try:
    from webcrawl import errors
except ImportError:                                  # 直接运行本文件（自测入口）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from webcrawl import errors

# ---------------------------------------------------------------- 常量区
OBJ_RE = re.compile(rb"(\d+)\s+\d+\s+obj(.*?)endobj", re.S)
STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
HEX_TEXT_RE = re.compile(rb"<([0-9A-Fa-f]+)>\s*Tj")
LIT_TEXT_RE = re.compile(rb"\((?:\\.|[^\\()])*\)\s*Tj")
ARRAY_TEXT_RE = re.compile(rb"\[(.*?)\]\s*TJ", re.S)
HEX_IN_ARRAY_RE = re.compile(rb"<([0-9A-Fa-f]+)>")
ENC_RE = re.compile(rb"/Encrypt\b")


def iter_objects(data: bytes) -> list[bytes]:
    return [body for _, body in OBJ_RE.findall(data)]


def decompress_streams(data: bytes) -> list[bytes]:
    """解压所有能解的流（FlateDecode）；解不了的跳过。"""
    out: list[bytes] = []
    for raw in STREAM_RE.findall(data):
        try:
            out.append(zlib.decompress(raw))
        except zlib.error:
            continue
    return out


def parse_cmaps(streams: list[bytes]) -> dict[int, str]:
    """收集所有 ToUnicode CMap，合并成 CID → Unicode。冲突时先到先得。"""
    mapping: dict[int, str] = {}
    for stream in streams:
        if b"beginbfchar" not in stream and b"beginbfrange" not in stream:
            continue
        for block in re.findall(rb"beginbfchar(.*?)endbfchar", stream, re.S):
            for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                try:
                    mapping.setdefault(int(src, 16),
                                        bytes.fromhex(dst.decode()).decode("utf-16-be", "ignore"))
                except (ValueError, UnicodeDecodeError):
                    continue
        for block in re.findall(rb"beginbfrange(.*?)endbfrange", stream, re.S):
            for lo, hi, dst in re.findall(
                    rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                try:
                    start, stop = int(lo, 16), int(hi, 16)
                    base = int.from_bytes(bytes.fromhex(dst.decode()), "big")
                except (ValueError, UnicodeDecodeError):
                    continue
                for offset in range(min(stop - start + 1, 4096)):
                    try:
                        mapping.setdefault(start + offset, chr(base + offset))
                    except ValueError:
                        continue
    return mapping


def _decode_hex(hex_bytes: bytes, cmap: dict[int, str]) -> str:
    text = hex_bytes.decode()
    if len(text) % 4:
        return ""
    out: list[str] = []
    for i in range(0, len(text), 4):
        code = int(text[i:i + 4], 16)
        out.append(cmap.get(code) or "")
    return "".join(out)


def extract_text(content: bytes, cmap: dict[int, str]) -> str:
    """从内容流里抽文本。支持 <hex> Tj、(literal) Tj、[ ... ] TJ。"""
    pieces: list[str] = []
    for match in HEX_TEXT_RE.finditer(content):
        pieces.append(_decode_hex(match.group(1), cmap))
    for match in ARRAY_TEXT_RE.finditer(content):
        for hex_bytes in HEX_IN_ARRAY_RE.findall(match.group(1)):
            pieces.append(_decode_hex(hex_bytes, cmap))
    for match in LIT_TEXT_RE.finditer(content):
        raw = match.group(0)
        inner = raw[1:raw.rfind(b")")]
        try:
            pieces.append(inner.decode("latin-1"))
        except Exception:
            continue
    return "".join(pieces)


def parse(data: bytes) -> dict[str, Any]:
    """PDF 字节 → {pages, text, page_map, error}。永不抛异常。"""
    result: dict[str, Any] = {"pages": 0, "text": "", "page_map": [], "error": None}

    if not data:
        result["error"] = errors.make(errors.E_PDF_PARSE, "PDF 内容为空")
        return result
    if ENC_RE.search(data):
        result["error"] = errors.make(errors.E_PDF_PARSE, "PDF 已加密，本期不解析")
        return result

    streams = decompress_streams(data)
    cmap = parse_cmaps(streams)

    # 内容流：含文本操作符的那些，按出现顺序当作"页"（近似，够用来定位段/页）
    content_streams = [s for s in streams if (b"Tj" in s or b"TJ" in s) and b"BT" in s]

    chunks: list[str] = []
    page_map: list[dict[str, int]] = []
    cursor = 0
    for page_no, stream in enumerate(content_streams, start=1):
        text = extract_text(stream, cmap).strip()
        if not text:
            continue
        start = cursor
        chunks.append(text)
        cursor = start + len(text) + 1
        page_map.append({"page": page_no, "char_start": start, "char_end": start + len(text)})

    result["text"] = "\n".join(chunks)
    result["page_map"] = page_map
    result["pages"] = len(page_map)

    if not result["text"]:
        result["error"] = errors.make(
            errors.E_PDF_NO_TEXT, "PDF 无文本层（扫描件），需 OCR，本期不处理")
    return result


def _self_test() -> None:
    """零网络自检：CMap 解析、hex 文本解码、空/加密/无文本层的分支。"""
    cmap_stream = (b"beginbfchar\n<0001> <4E2D>\n<0002> <6587>\nendbfchar\n"
                   b"beginbfrange\n<0010> <0012> <0030>\nendbfrange")
    cmap = parse_cmaps([cmap_stream])
    assert cmap[0x0001] == "中" and cmap[0x0002] == "文", cmap
    assert cmap[0x0010] == "0" and cmap[0x0012] == "2", cmap

    content = b"BT <0001> Tj <0002> Tj ET"
    assert extract_text(content, cmap) == "中文", extract_text(content, cmap)
    assert extract_text(b"[<0010><0011>] TJ", cmap) == "01"
    assert extract_text(b"(hello) Tj", {}) == "hello"

    assert parse(b"")["error"]["code"] == errors.E_PDF_PARSE
    assert parse(b"1 0 obj << >> endobj /Encrypt")["error"]["code"] == errors.E_PDF_PARSE

    empty = parse(b"1 0 obj << >> endobj")
    assert empty["error"]["code"] == errors.E_PDF_NO_TEXT, empty
    print("[OK] pdf 自检通过：CMap / hex 解码 / 三种失败分支（零网络）")


if __name__ == "__main__":
    _self_test()
