# -*- coding: utf-8 -*-
"""正文容器定位 —— 整页可见文字 → 正文容器内的文字（去掉导航 / 页脚 / 侧栏 / 推荐位 / 评论 / 分享）。

为什么需要这一步：html_utils.to_text 抽的是"整页可见文字"，导航和页脚会一起算进正文，
下游按整页文字算字数、抽数字事实、判重都会失真（见 AGENTS.md §7 坑表两条）。

本文件是纯函数模块：不联网、不读文件、不 import 项目内其它模块；
唯一依赖是共用层的 html_utils（同样只吃标准库），import 失败时按"直接运行"补路径。

================================ 结构树 =================================
① 常量（公开，调用方复用同一套词表，避免各写一份）
    NOISE_TAGS                整块删除的噪声标签（nav/header/footer/aside/form/iframe/…）
    NOISE_PATTERNS            class/id 噪声词表（nav/menu/footer/sidebar/comment/share/…）
    ROLE_NOISE                role 噪声角色（navigation/banner/contentinfo/complementary/…）
    HINT_PATTERNS             class/id 正文正向词表（article/content/main/body/post/…）
    VOID_TAGS                 空元素标签（不参与嵌套配对）
    HARD_NOISE_TAGS           未闭合时删到文末才安全的标签（script/style/svg/…）
    CANDIDATE_TAGS            参与打分的候选容器标签（body/main/article/section/div/…）
② 切词与骨架（内部）
    _scan_tokens(html)        切出注释 / 原始文本块 / 普通起止标签（保留原串偏移）
    _build_frames(tokens, n)  按标签名做嵌套深度配对 → 元素骨架（容错畸形 HTML）
    _attrs_value(attrs, name) 从属性串取 class / id / role 原值
    _tokens_of(value)         class/id 原值 → 小写词元（按非字母数字切开）
    _is_noise_tag_or_attrs    标签 / class / id / role 任一命中噪声即算噪声元素
    _merge_ranges(ranges, n)  合并重叠删除区间并按位置排序
③ 去噪
    strip_noise(html)         标签 + class/id 深度配对删除噪声块，字符串切片，不碰正文
④ 文字度量
    _MeasureParser            单片段扫一遍：可见文字 / 链接文字 / 段落数 / 链接数
    _measure(fragment)        可见文字、字符数、链接密度、标点、段落、链接数
    text_len(fragment)        可见文本字符数（去掉全部空白）
    link_density(fragment)    <a> 可见文字 / 全部可见文字，0.0~1.0
⑤ 候选与定位
    _hint_score(frame)        class/id 正向词命中数 + article/main 标签加分
    _score_cleaned(html)      对已去噪 HTML 的候选容器逐个打分并降序
    score_blocks(html)        strip_noise 后打分（公开入口）
    _kind_of(candidate)       候选容器 → article / listing（命中 article/content 词优先判文章）
    _describe(candidate)      候选容器 → 一行中文说明（写进 reason）
    _fallback(raw, reason)    unknown 兜底（html 原样回退，绝不删空）
    find_main(html)           选正文主容器 → {html, kind, score, reason, container, candidates}
    article_body(html)        find_main(html)["html"]：正文容器 HTML
⑥ 自测
    _self_test()              零网络，只吃内置字符串

================================ 调用关系 ================================
find_main ──▶ strip_noise ──▶ _scan_tokens / _build_frames
           └─▶ score_blocks ──▶ _score_cleaned ──▶ _measure / _hint_score
article_body ──▶ find_main
_self_test ──▶ article_body / find_main / strip_noise / text_len / link_density
下一阶段接入方向（本阶段只新增、不改旧文件）：
    normalize.to_paragraphs(article_body(html))        正文段落锚点
    extract.Extract_Article.text_from_html(article_body(html))   正文纯文本

================================ 数据流 =================================
HTML 字节 → 解码后 str → strip_noise → 无噪声 HTML → score_blocks → 候选容器
                                   └─▶ find_main → article_body → 正文 HTML → to_text → 段落

================================ 返回契约 ==============================
strip_noise(html) -> str            去噪后的 HTML；一处都删不到时原样返回
score_blocks(html) -> list[dict]    按得分降序（同分按出现位置），无候选返回 []
find_main(html) -> dict             六个键固定存在；无候选 / 正文过短时 html 回退原文
article_body(html) -> str           find_main(html)["html"]
text_len(fragment) -> int           去空白后的可见字符数
link_density(fragment) -> float     [0.0, 1.0]；片段没有可见文字时为 0.0
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    from webcrawl import html_utils
except ImportError:                                  # 直接运行本文件（自测入口）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from webcrawl import html_utils

# ---------------------------------------------------------------- 常量区
# 噪声标签：整块删除。figure 故意不在这里——图注（figcaption）常是正文证据，
# 只有它自身 class/id 命中噪声词表时才删。
NOISE_TAGS = frozenset({
    "nav", "header", "footer", "aside", "form", "iframe", "script", "style",
    "noscript", "template", "svg", "canvas", "dialog", "menu", "button",
    "select", "textarea", "input", "video", "audio",
})

# class/id 噪声词表。匹配是"词元级"而不是子串级：class="ad-banner" 先切成
# ad / banner 再命中 ad（这就是需求里 "ad-" 的落地方式），而 class="commentary"
# 切出 commentary，不会误中 comment。
NOISE_PATTERNS = frozenset({
    "nav", "navbar", "navigation", "navi", "menu", "submenu", "menubar",
    "topnav", "mainnav", "sitemap", "breadcrumb", "breadcrumbs", "crumb", "crumbs",
    "header", "pageheader", "siteheader", "masthead", "topbar", "banner",
    "footer", "pagefooter", "sitefooter", "copyright",
    "sidebar", "side", "aside", "leftbar", "rightbar", "sidebox",
    "comment", "comments", "commentlist", "reply", "replies", "pinglun",
    "share", "sharing", "sharebar", "social", "follow", "subscribe",
    "qrcode", "qr", "print", "gotop", "backtop", "totop", "toolbar", "tool", "tools",
    "related", "relate", "recommend", "recommendation", "recommended",
    "hot", "popular", "rank", "ranking",
    "ad", "ads", "adv", "advert", "advertisement", "advertising", "advertorial",
    "adbox", "adslot", "adbanner", "adunit", "adzone", "adcontainer", "adwrapper",
    "adsbygoogle", "sponsor", "sponsored", "promo", "promotion",
    "carousel", "slider", "swiper", "marquee", "pagination", "pager", "paging",
    "login", "signin", "register", "popup", "modal", "cookie",
    "search", "searchbox", "searchform", "links", "linklist", "friendlinks",
    "tags", "tagcloud", "keywords",
})

ROLE_NOISE = frozenset({
    "navigation", "banner", "contentinfo", "complementary", "search", "dialog",
    "alertdialog", "menu", "menubar",
})

# class/id 正向词表：命中越多，越可能是正文容器；article/main 标签本身再 +1。
HINT_PATTERNS = frozenset({
    "article", "content", "main", "body", "post", "entry", "detail",
    "artibody", "articlebody", "text", "story", "rich", "media",
})

# 空元素：没有闭合标签，不压栈；若命中噪声则只删标签本身。
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "command", "embed", "hr", "img", "input",
    "keygen", "link", "meta", "param", "source", "track", "wbr",
})

# 这些标签即便没闭合也必须删到文末：否则脚本/样式源码会被当成正文文字。
HARD_NOISE_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "canvas"})

CANDIDATE_TAGS = frozenset({
    "body", "main", "article", "section", "div", "td", "table", "center", "ul", "ol", "dl",
})

# 标签切词：注释 / CDATA / 声明 / 原始文本块（script/style/noscript/template/svg 整段）
# 各占一个分支；普通起止标签保留属性原串，便于后面按 class/id/role 判定。
TOKEN_RE = re.compile(
    r"<!--(?P<comment>.*?)-->"
    r"|<!\[CDATA\[(?P<cdata>.*?)\]\]>"
    r"|<![^>]*>"
    r"|<\?[^>]*\?>"
    r"|<(?P<rawtag>script|style|noscript|template|svg)\b"
    r"(?P<rawattrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)>"
    r"(?P<rawbody>.*?)</(?P=rawtag)\s*>"
    r"|<(?P<close>/?)(?P<tag>[A-Za-z][\w:.-]*)"
    r"(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(?P<selfclose>/?)>",
    re.S | re.I,
)

ATTR_RE = re.compile(
    r"""([A-Za-z_:][-\w:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
    re.S,
)
WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")
PUNCT_CHARS = "，。！？；：、,.!?;:"

MIN_MAIN_CHARS = 40          # 主容器可见字符低于此值 → 判为无正文，回退原文
LISTING_DENSITY = 0.5        # 链接密度 ≥ 这个值 → 列表页
LISTING_MIN_LINKS = 8        # 链接数够多且密度 ≥ 0.3 也判列表页
TIGHTEN_RATIO = 0.9          # 得分不低于最高分 90% 的候选里，选最紧凑的一个
CANDIDATE_LIMIT = 20         # find_main 最多回传多少个候选，防止结果膨胀
PARA_BONUS = 25.0            # 每个 <p> 加分
PUNCT_BONUS = 3.0            # 每个句读标点加分
HINT_BONUS = 50.0            # 每个 class/id 正向词加分


# ============================================ ① 切词与骨架（内部）
def _scan_tokens(html: str) -> list[dict[str, Any]]:
    """把 HTML 切成带原串偏移的标签词元（注释 / 原始文本块 / 普通起止标签）。"""
    tokens: list[dict[str, Any]] = []
    for match in TOKEN_RE.finditer(html):
        comment, cdata = match.group("comment"), match.group("cdata")
        if comment is not None or cdata is not None:
            tokens.append({
                "kind": "comment" if comment is not None else "cdata",
                "tag": None, "attrs": "",
                "start": match.start(), "end": match.end(),
            })
            continue
        raw_tag = match.group("rawtag")
        if raw_tag is not None:                      # script/style/… 整段一个词元，内部不再切
            tokens.append({
                "kind": "raw", "tag": raw_tag.lower(),
                "attrs": match.group("rawattrs") or "",
                "start": match.start(), "end": match.end(),
            })
            continue
        tag = match.group("tag")
        if tag is None:                              # DOCTYPE / 处理指令等，不是元素
            continue
        tag = tag.lower()
        attrs = match.group("attrs") or ""
        selfclose = bool(match.group("selfclose"))
        if attrs.rstrip().endswith("/"):             # <br/> 这种斜杠可能被属性串吃掉
            attrs = attrs.rstrip()[:-1]
            selfclose = True
        if match.group("close"):
            tokens.append({
                "kind": "end", "tag": tag, "attrs": "",
                "start": match.start(), "end": match.end(),
            })
        elif selfclose or tag in VOID_TAGS:
            tokens.append({
                "kind": "void", "tag": tag, "attrs": attrs,
                "start": match.start(), "end": match.end(),
            })
        else:
            tokens.append({
                "kind": "start", "tag": tag, "attrs": attrs,
                "start": match.start(), "inner_start": match.end(), "end": match.end(),
            })
    return tokens


def _build_frames(tokens: list[dict[str, Any]], html_len: int) -> list[dict[str, Any]]:
    """按标签名做嵌套深度配对，产出元素骨架（起止标签不配对时不丢整页）。

    配对规则与浏览器容错同款：结束标签从栈顶往下找同名标签，中间没闭合的标签
    按"隐式关闭"处理；全页扫完仍未闭合的，内容记为到文末。
    """
    frames: list[dict[str, Any]] = []
    stack: list[int] = []
    for token in tokens:
        kind = token["kind"]
        if kind == "start":
            frames.append({
                "tag": token["tag"], "attrs": token["attrs"],
                "start": token["start"], "inner_start": token["inner_start"],
                "inner_end": html_len, "close_end": html_len,
                "closed": False, "implicit": False,
            })
            stack.append(len(frames) - 1)
            continue
        if kind != "end":
            continue
        for position in range(len(stack) - 1, -1, -1):        # 从栈顶往下找同名标签
            if frames[stack[position]]["tag"] != token["tag"]:
                continue
            for index in stack[position + 1:]:                 # 中间没闭合的：隐式关闭
                frame = frames[index]
                frame["inner_end"] = token["start"]
                frame["close_end"] = token["start"]
                frame["implicit"] = True
            frame = frames[stack[position]]
            frame["inner_end"] = token["start"]
            frame["close_end"] = token["end"]
            frame["closed"] = True
            del stack[position:]
            break
    return frames


def _attrs_value(attrs: str, name: str) -> str:
    """从属性原串取某个属性的值（没有就返回空串）。"""
    for match in ATTR_RE.finditer(attrs or ""):
        if match.group(1).lower() != name:
            continue
        return match.group(2) or match.group(3) or match.group(4) or ""
    return ""


def _tokens_of(value: str) -> list[str]:
    """class/id 原值 → 小写词元（按非字母数字切开，避免 commentary 误中 comment）。"""
    return [word for word in WORD_SPLIT_RE.split((value or "").lower()) if word]


def _is_noise_tag_or_attrs(tag: str, attrs: str) -> bool:
    """标签名命中 NOISE_TAGS，或 class/id 词元命中 NOISE_PATTERNS，或 role 是噪声角色。"""
    if tag.lower() in NOISE_TAGS:
        return True
    words = _tokens_of(_attrs_value(attrs, "class")) + _tokens_of(_attrs_value(attrs, "id"))
    if any(word in NOISE_PATTERNS for word in words):
        return True
    return _attrs_value(attrs, "role").strip().lower() in ROLE_NOISE


def _merge_ranges(ranges: list[tuple[int, int]], limit: int) -> list[tuple[int, int]]:
    """合并重叠/相邻的删除区间，按起点排序并夹在 [0, limit] 内。"""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        start = max(0, min(start, limit))
        end = max(0, min(end, limit))
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# ============================================ ② 去噪
def strip_noise(html: str) -> str:
    """按标签与 class/id/role 删除噪声块（深度配对，不是正则贪婪匹配）。

    删除的是**整段原串区间**：`<div class="nav">…</div>` 从起标签删到配对闭标签，
    嵌套的子块一并带走；未闭合标签按 `_build_frames` 的容错规则处理——
    script/style 这类删到文末，nav/footer 这类只删起标签（宁可留下文字，不误删正文）。
    """
    source = html or ""
    if not source:
        return ""
    tokens = _scan_tokens(source)
    frames = _build_frames(tokens, len(source))
    ranges: list[tuple[int, int]] = []
    for token in tokens:
        kind = token["kind"]
        if kind in ("comment", "cdata", "raw"):            # 注释与脚本样式整段删
            ranges.append((token["start"], token["end"]))
        elif kind == "void" and _is_noise_tag_or_attrs(token["tag"], token["attrs"]):
            ranges.append((token["start"], token["end"]))
    for frame in frames:
        if not _is_noise_tag_or_attrs(frame["tag"], frame["attrs"]):
            continue
        if frame["closed"]:
            ranges.append((frame["start"], frame["close_end"]))
        elif frame["implicit"]:
            # 隐式关闭 = 源码缺闭标签，按"整段删"会连正文一起删（真踩过：qdnrm.cn 导航壳
            # 未闭合，配对到 body 闭标签，整页正文被当成导航删光）。只有内部基本是链接
            # （菜单/页脚/推荐位）才整段删；否则只删起标签，把文字留给候选打分去判断。
            metrics = _measure(source[frame["inner_start"]:frame["inner_end"]])
            if metrics["link_density"] >= 0.5:
                ranges.append((frame["start"], frame["close_end"]))
            else:
                ranges.append((frame["start"], frame["inner_start"]))
        elif frame["tag"] in HARD_NOISE_TAGS:
            ranges.append((frame["start"], len(source)))
        else:
            ranges.append((frame["start"], frame["inner_start"]))
    merged = _merge_ranges(ranges, len(source))
    if not merged:
        return source
    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        if start > cursor:
            pieces.append(source[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(source):
        pieces.append(source[cursor:])
    return "".join(pieces)


# ============================================ ③ 文字度量
class _MeasureParser(HTMLParser):
    """单个片段扫一遍：可见文字、链接文字、段落数、链接数。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.link_parts: list[str] = []
        self.paragraphs = 0
        self.links = 0
        self.anchor_depth = 0
        self.skip_depth = 0              # >0 表示正处在 script/style/head 等跳过标签里

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in html_utils.SKIP_TAGS:
            self.skip_depth += 1
        elif tag == "a":
            self.links += 1
            self.anchor_depth += 1
        elif tag == "p":
            self.paragraphs += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in html_utils.SKIP_TAGS:
            if self.skip_depth > 0:
                self.skip_depth -= 1
        elif tag == "a" and self.anchor_depth > 0:
            self.anchor_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = data.strip()
        if not text:
            return
        self.parts.append(text)
        if self.anchor_depth:
            self.link_parts.append(text)


def _measure(fragment: str) -> dict[str, Any]:
    """片段 → 可见文字、去空白字符数、链接密度、标点数、段落数、链接数。"""
    parser = _MeasureParser()
    try:
        parser.feed(fragment or "")
        parser.close()
    except Exception:                    # 畸形 HTML 不让整批挂掉（与 html_utils 一致）
        pass
    text = html_utils.SPACE_RE.sub(" ", " ".join(parser.parts)).strip()
    compact = "".join(text.split())
    link_text = "".join("".join(parser.link_parts).split())
    density = (len(link_text) / len(compact)) if compact else 0.0
    punct = sum(text.count(char) for char in PUNCT_CHARS)
    return {
        "text": text,
        "text_len": len(compact),
        "link_density": density,
        "punct": punct,
        "paragraphs": parser.paragraphs,
        "link_count": parser.links,
    }


def text_len(fragment: str) -> int:
    """可见文本字符数（去掉全部空白；HTML 标签与脚本样式不算）。"""
    return _measure(fragment)["text_len"]


def link_density(fragment: str) -> float:
    """链接密度 = <a> 内可见文字 / 全部可见文字，0.0~1.0；没有可见文字返回 0.0。"""
    return _measure(fragment)["link_density"]


# ============================================ ④ 候选与定位
def _hint_score(frame: dict[str, Any]) -> int:
    """class/id 正向词命中数（最多算 3 个）+ article/main 标签本身 1 分。"""
    words = (_tokens_of(_attrs_value(frame["attrs"], "class"))
             + _tokens_of(_attrs_value(frame["attrs"], "id")))
    hits = min(sum(1 for word in words if word in HINT_PATTERNS), 3)
    if frame["tag"] in ("article", "main"):
        hits += 1
    return hits


def _score_cleaned(html: str) -> list[dict[str, Any]]:
    """对已去噪 HTML 的候选容器逐个打分，按得分降序、同分按出现位置返回。"""
    tokens = _scan_tokens(html)
    frames = _build_frames(tokens, len(html))
    scored: list[dict[str, Any]] = []
    for frame in frames:
        if frame["tag"] not in CANDIDATE_TAGS:
            continue
        inner = html[frame["inner_start"]:frame["inner_end"]]
        if not inner.strip():
            continue
        metrics = _measure(inner)
        if metrics["text_len"] <= 0:
            continue
        hint = _hint_score(frame)
        score = metrics["text_len"] * (1.0 - min(metrics["link_density"], 1.0))
        score += metrics["paragraphs"] * PARA_BONUS
        score += metrics["punct"] * PUNCT_BONUS
        score += hint * HINT_BONUS
        scored.append({
            "tag": frame["tag"],
            "class": " ".join(_attrs_value(frame["attrs"], "class").split()),
            "id": " ".join(_attrs_value(frame["attrs"], "id").split()),
            "score": round(score, 2),
            "hint": hint,
            "text_len": metrics["text_len"],
            "link_density": round(metrics["link_density"], 3),
            "paragraphs": metrics["paragraphs"],
            "punct": metrics["punct"],
            "link_count": metrics["link_count"],
            "start": frame["start"],
            "end": frame["close_end"],
            "inner_start": frame["inner_start"],
            "inner_end": frame["inner_end"],
        })
    scored.sort(key=lambda candidate: (-candidate["score"], candidate["start"]))
    return scored


def score_blocks(html: str) -> list[dict[str, Any]]:
    """候选容器打分：先 strip_noise 去噪，再对每个候选块打分并降序返回。

    打分依据：文本长度 × (1 - 链接密度) + 段落数 × 25 + 标点数 × 3 + 正向词命中 × 50。
    """
    return _score_cleaned(strip_noise(html or ""))


def _kind_of(candidate: dict[str, Any]) -> str:
    """候选容器归类：写了 article/content 的容器 → article，否则按链接密度判列表页。"""
    if candidate.get("hint", 0) >= 2:            # class/id 明确自称正文，链接再多也是文章
        return "article"
    if candidate["link_density"] >= LISTING_DENSITY:
        return "listing"
    if (candidate["link_count"] >= LISTING_MIN_LINKS
            and candidate["link_density"] >= 0.3):
        return "listing"
    return "article"


def _describe(candidate: dict[str, Any]) -> str:
    """候选容器 → 一行中文说明（写进 find_main 的 reason）。"""
    label = candidate["tag"]
    if candidate["class"]:
        label += f' class="{candidate["class"]}"'
    elif candidate["id"]:
        label += f' id="{candidate["id"]}"'
    return (f"<{label}> 文本 {candidate['text_len']} 字、链接密度 "
            f"{candidate['link_density']:.3f}、段落 {candidate['paragraphs']} 个、"
            f"得分 {candidate['score']}")


def _fallback(raw: str, reason: str,
              candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """unknown 兜底：html 原样回退（宁可带着噪声，也不把正文删空）。"""
    return {
        "html": raw,
        "kind": "unknown",
        "score": 0.0,
        "reason": reason,
        "container": None,
        "candidates": (candidates or [])[:CANDIDATE_LIMIT],
    }


def find_main(html: str) -> dict[str, Any]:
    """定位正文主容器，返回 {html, kind, score, reason, container, candidates}。

    - kind：article（文章页）/ listing（链接列表页）/ unknown（没找到或正文过短）；
      归类看**选中的容器**，不看页面里分最高的外层链接壳
    - html：选中容器的内部 HTML；unknown 时**原样返回传入的 html**
    - container：选中容器的描述（tag/class/id/得分/度量），unknown 时为 None
    - candidates：按得分降序的候选（最多 CANDIDATE_LIMIT 个）
    """
    raw = html or ""
    cleaned = strip_noise(raw)
    candidates = _score_cleaned(cleaned)
    if not candidates:
        return _fallback(raw, "未找到候选容器（页面没有 body/main/div 等块级标签），回退原文")
    best = candidates[0]
    if best["text_len"] < MIN_MAIN_CHARS:
        return _fallback(
            raw,
            f"最高分候选正文仅 {best['text_len']} 字（低于 {MIN_MAIN_CHARS} 字），"
            f"判为无正文，回退原文",
            candidates,
        )
    tightened = [c for c in candidates if c["score"] >= best["score"] * TIGHTEN_RATIO]
    # 得分接近时取最紧凑的容器（同文字量取更靠里的那个），避免把整页 body 当正文
    chosen = min(tightened, key=lambda candidate: (candidate["text_len"], -candidate["start"]))
    kind = _kind_of(chosen)                      # 归类跟"选中的容器"走，不跟最高分容器走
    body_html = cleaned[chosen["inner_start"]:chosen["inner_end"]]
    container = {
        key: chosen[key]
        for key in ("tag", "class", "id", "score", "text_len", "link_density",
                    "paragraphs", "link_count", "start", "end")
    }
    return {
        "html": body_html,
        "kind": kind,
        "score": best["score"],
        "reason": f"选中 {_describe(chosen)}，归类 {kind}；导航/页脚等噪声已按词表删除",
        "container": container,
        "candidates": candidates[:CANDIDATE_LIMIT],
    }


def article_body(html: str) -> str:
    """正文容器 HTML（find_main 的 html）；找不到容器时原样返回传入的 HTML。"""
    return find_main(html)["html"]


# ============================================ ⑤ 自测
def _self_test() -> None:
    """零网络自检：噪声删除 / 主容器定位 / 列表页归类 / 回退与稳定性。"""
    nav_links = "".join(f'<a href="/c{index}">栏目{index}</a>' for index in range(20))
    html = (
        "<!DOCTYPE html><html><head><title>测试文章</title></head><body>"
        f'<header class="site-header"><a href="/">首页</a></header>'
        f'<div class="nav">{nav_links}</div>'
        '<div class="main-content"><div class="article">'
        "<h1>产业调查</h1><div class=\"article-content\">"
        "<p>第一段：凯里酸汤鱼是地方特色餐饮，产业规模持续扩大。</p>"
        "<p>第二段：2025年餐饮收入 412.68 亿元，同比增长 7.9%。</p>"
        "<p>第三段：上游木姜子种植面积约 3.2 万亩，加工企业 87 家。</p>"
        "</div>"
        '<div class="share-bar"><a href="/share">分享</a></div>'
        "</div>"
        '<aside class="sidebar"><a href="/hot">热门推荐</a></aside>'
        "</div>"
        '<footer class="site-footer">版权所有 测试站点</footer>'
        '<script>var fake = "<div>假标签</div>";</script>'
        "</body></html>"
    )
    cleaned = strip_noise(html)
    assert "栏目7" not in cleaned and "首页" not in cleaned, cleaned[:200]
    assert "第三段" in cleaned, cleaned[:200]

    result = find_main(html)
    body = result["html"]
    for paragraph in ("第一段", "第二段", "第三段"):
        assert paragraph in body, (paragraph, body[:200])
    assert "栏目7" not in body and "热门推荐" not in body and "版权所有" not in body, body
    assert "假标签" not in body, body
    assert result["kind"] == "article", result["kind"]
    assert result["candidates"], result
    assert result["candidates"][0]["score"] >= result["candidates"][-1]["score"], result

    # 回退：没有块级容器 / 正文太短，都不能删空
    plain = "这是一段没有任何块级标签的纯文本。"
    assert find_main(plain)["kind"] == "unknown", find_main(plain)
    assert article_body(plain) == plain, article_body(plain)
    short = "<body><div>短</div></body>"
    assert find_main(short)["kind"] == "unknown", find_main(short)
    assert article_body(short) == short, article_body(short)

    # 列表页：链接密度高 → listing
    listing = "<html><body><div class=\"news-list\">" + "".join(
        f'<p><a href="/a{index}">新闻标题{index}</a></p>' for index in range(25)
    ) + "</div></body></html>"
    assert find_main(listing)["kind"] == "listing", find_main(listing)

    # 度量与稳定性：同一份 HTML 重跑，结果必须逐字节一致
    assert text_len("<p>你好 <b>world</b></p>") == 7, text_len("<p>你好 <b>world</b></p>")
    assert link_density('<a href="/x">链接</a>') == 1.0, link_density('<a href="/x">链接</a>')
    first = find_main(html)
    second = find_main(html)
    assert first == second, "同一 HTML 重跑结果不一致"
    assert article_body(html) == body, "article_body 与 find_main 不一致"

    print("[OK] boilerplate 自检通过：噪声删除 / 主容器定位 / 列表归类 / 回退与稳定性（零网络）")


if __name__ == "__main__":
    _self_test()
