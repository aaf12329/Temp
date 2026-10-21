# -*- coding: utf-8 -*-
"""控制台输出 —— 全项目唯一的「终端显示」适配层。

为什么单独一个文件：Windows 控制台默认是 GBK，打印中文/特殊符号会崩
（`UnicodeEncodeError: 'gbk' codec can't encode`）。原先 4 个模块各抄了一遍
`_force_utf8_output`，这里合并成一份。

============================== 函数结构树 ==============================
    force_utf8_output()      把 stdout / stderr 切成 UTF-8（失败静默）
    print_block(lines)       打印一段带边框的摘要
    print_kv(pairs, width)   打印「键 : 值」对齐的多行

============================== 调用关系总览 ==============================
各模块的 main() ──▶ force_utf8_output()      （进入 CLI 前调一次）

==================================== 说明 ====================================
本模块不 import 项目内任何东西，纯标准库。
"""

from __future__ import annotations

import sys
from typing import Iterable, Sequence

LINE_WIDTH = 78


def force_utf8_output() -> None:
    """把终端输出改成 UTF-8，避开 Windows 默认 GBK。流不支持时静默跳过。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def print_block(lines: Iterable[str]) -> None:
    """打印一段带上下边框的摘要（内容原样输出，不做任何加工）。"""
    bar = "=" * LINE_WIDTH
    print(bar)
    for line in lines:
        print(line)
    print(bar)
    print()


def print_kv(pairs: Sequence[tuple[str, object]], width: int = 10) -> None:
    """打印「键 : 值」，键左对齐到 `width` 个显示宽度（中文按 2 计）。"""
    for key, value in pairs:
        pad = width - _display_width(key)
        print(f"{key}{' ' * max(pad, 1)}: {value}")


def _display_width(text: str) -> int:
    """粗略算显示宽度：中日韩字符按 2 个字符宽计。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
