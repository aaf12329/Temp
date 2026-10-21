# -*- coding: utf-8 -*-
"""HTML 文本工具 —— 全项目唯一的「HTML → 可见文本 / 标题」实现。

《工程架构规范》§12：本文件属于共用层，只提供纯函数与一个内部解析器；
不读写文件、不联网、不 import 项目内任何模块，可离线单测。

================================ 函数结构树 ================================
① 常量（公开，调用方复用同一套正则，避免各写一份）
    SKIP_TAGS                 不参与可见文本的标签集合
    SPACE_RE                  空白归一化（连续空白压成单空格）
    TITLE_RE                  <title> 文本提取（跨行、忽略大小写）
② 解析器（内部）
    _VisibleTextParser        扫一遍 HTML，只收集可见文本
        ├─ handle_starttag    进入跳过标签 → 跳过深度 +1
        ├─ handle_endtag      离开跳过标签 → 跳过深度 -1
        └─ handle_data        深度为 0 时，收下 strip 后的非空文本
③ 公开函数
    to_text(html)             HTML → 可见正文纯文本
    parse_title(html)         HTML → 归一化后的标题 / None

================================ 调用关系总览 ==============================
本阶段只新增、不改旧文件，因此项目内暂无调用方；下一阶段接入方向：
    Robots_detect.Robots_Detect.html_text ──▶ to_text
    extract.Extract_Article.text_from_html ──▶ to_text
    extract.Extract_Article.parse_title    ──▶ parse_title

本文件内部：
    to_text ──▶ _VisibleTextParser.feed / close
                  └─▶ handle_starttag / handle_endtag / handle_data
    parse_title ──▶ TITLE_RE.search ──▶ SPACE_RE.sub

本文件不 import 项目内任何模块（共用层，零项目依赖）。

================================= 数据流 =================================
HTML 字节 → 解码后的 str → to_text → 可见文本 → 字数 / 页面类型判定 / 正文抽取
HTML 字节 → 解码后的 str → parse_title → 归一化标题 → 文章字段 title

================================= 合并说明 ===============================
本文件合并了原先 robots 判定层与抽取层的两份重复实现：
    Robots_detect.Robots_Detect._VisibleTextParser + html_text
    extract.Extract_Article._HtmlText + text_from_html
改这里等于同时改两处。合并后统一采用抽取层语义：每段先 strip、非空才
收集，最后用空格 join 并归一化空白；跳过标签改用嵌套深度计数，畸形 HTML
不会像旧抽取层的列表栈那样错位。to_text 兜住解析异常，单页失败不拖垮整批。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# ---------------------------------------------------------------- 常量区
# head 必须跳过：<title> / <meta> 里的文字浏览器根本不渲染，算进正文会让
# 文章字数虚高、标题混进数字事实抽取（合并两份实现时漏过这一条，已回归）
SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "head"})
SPACE_RE = re.compile(r"\s+")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


# ============================================ ① 解析器（内部）
class _VisibleTextParser(HTMLParser):
    """扫一遍 HTML，只收集**会被渲染出来**的文本，跳过脚本样式与 head。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth: int = 0         # >0 表示正处在要跳过的标签里

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """进入跳过标签时加深层级（嵌套标签各自计数）。"""
        if tag.lower() in SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        """离开跳过标签时回退一层；多余的结束标签不把深度减成负数。"""
        if tag.lower() in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        """跳过深度为 0 时，只收下 strip 后非空的文本段。"""
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text)


# ============================================ ② 公开函数
def to_text(html: str) -> str:
    """HTML → 可见正文纯文本（跳过脚本样式，空白归一化成单空格）。"""
    scanner = _VisibleTextParser()
    try:
        scanner.feed(html or "")
        scanner.close()
    except Exception:                    # 畸形 HTML 不让整批挂掉（与旧抽取层一致）
        pass
    return SPACE_RE.sub(" ", " ".join(scanner.parts)).strip()


def parse_title(html: str) -> str | None:
    """取 <title> 文本并归一化空白；没有就返回 None。"""
    match = TITLE_RE.search(html or "")
    if not match:
        return None
    title = SPACE_RE.sub(" ", match.group(1)).strip()
    return title or None
