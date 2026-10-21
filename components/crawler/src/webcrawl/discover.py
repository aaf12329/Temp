# -*- coding: utf-8 -*-
"""链接发现 —— 从栏目页 / 列表页 / sitemap 展开出「该抓哪些页面」。

种子 URL 只是入口：真正要落盘的是**详情页正文**和**附件**，
所以采集链路必须先回答"从这一页能找到哪些下一页"。

本模块只做**纯逻辑**：吃 HTML / XML / robots 文本，吐 URL 清单；
不联网、不读项目文件、不落盘。同一份输入重跑必须得到完全一样的结果
（无时间戳、无随机数、不依赖 set 遍历顺序）。

================================ 结构树 =================================
① 链接解析
    _AnchorParser              <a> 收集器（href / rel / 文字）
    extract_links(html, base)  HTML → [{url,text,rel,is_attachment}]
    is_attachment(url)         URL → 是否附件（按 LINK_EXT 后缀）
② 站点约束
    same_site(urls, seed)      只留与 seed 同站的条目（www 归一）
③ 页面分类
    list_detail_links(...)     列表页 → 详情候选（排导航 / 栏目根 / 外站）
    next_pages(html, base)     翻页链接（rel=next / 下一页 / page=N）
    attachments(html, base)    → [{url,ext,text}]
④ 站点地图
    parse_sitemap(xml)         urlset / sitemapindex（命名空间无关）
    parse_robots_sitemaps(txt) robots 里的 Sitemap: 行
⑤ 入池
    merge_pool(existing, new, limit)  去重 / 限深 / 限数 / 稳定顺序
⑥ 自测
    _self_test()               零网络

================================ 调用关系 ================================
（本阶段只建不调用）将来由采集编排层把 list_detail_links / next_pages /
attachments 的结果交给 merge_pool 入池，再逐条交给抓取层。

================================ 对外返回契约 =============================
extract_links     list[dict]  键固定：url / text / rel / is_attachment
list_detail_links list[dict]  键固定：url / text / score（越大越像详情页）
next_pages        list[str]   去重后的绝对 URL，仅同站
attachments       list[dict]  键固定：url / ext / text
parse_sitemap     dict        键固定：urls / sitemaps
merge_pool        dict        键固定：pool / dropped
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

try:
    from webcrawl import url_norm
except ImportError:                                  # 直接运行本文件（自测入口）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from webcrawl import url_norm

# ---------------------------------------------------------------- 常量区
# 附件后缀（不带点，统一小写）；只放"下载后要另存"的类型，图片/音视频不算。
LINK_EXT = frozenset({
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "csv", "txt", "rtf", "wps", "et", "dps", "ofd",
    "zip", "rar", "7z", "tar", "gz",
})

# 列表页里"不是详情"的导航词；比较前会先去掉空白与装饰符号。
NAV_WORDS = frozenset({
    "首页", "主页", "网站首页", "登录", "登陆", "注册", "注册登录",
    "更多", "查看更多", "上一页", "上页", "前一页", "前一頁",
    "下一页", "下页", "后页", "后一页", "末页", "尾页",
    "关于我们", "联系我们", "网站地图", "站点地图", "english",
    "返回", "返回首页", "返回列表", "关闭", "打印", "分享",
})

# 翻页文字提示；URL 里的 page=N 由 _PAGE_QUERY_RE 另行识别。
PAGINATION_HINTS = ("下一页", "下页", "后页", "后一页", "下一頁", "next", "»", "›")

# 详情页 URL 的常见特征；只用于打分，不做硬过滤（站改版时不至于一条不剩）。
DETAIL_HINTS = ("/article", "/content", "/detail", "/news", "/post", "/info",
                "/show", "/view", "/read", "/html", ".html", ".htm", ".shtml",
                "id=", "aid=", "articleid=", "docid=")

# 池内深度上限：栏目页(0) → 列表页(1) → 详情页(2) → 附件页(3)，再深基本是噪声。
MAX_DEPTH = 3

MAX_LINK_TEXT = 200             # 单个链接文字上限，防超长锚文本把内存撑爆
_INDEX_NAMES = frozenset({
    "index.html", "index.htm", "index.shtml", "index.php", "index.aspx",
    "default.html", "default.htm", "default.aspx",
})
_NAV_TRIM_RE = re.compile(r"[\s<>《》【】\[\]()（）»«›‹…|｜·•\-—_+/]+")
_PAGE_QUERY_RE = re.compile(
    r"(?:^|&)(?:page|pageno|pagenum|page_no|page_index|pageindex|paged)=(\d+)",
    re.IGNORECASE,
)


# ============================================================ ① 链接解析
def _clean_text(value: str) -> str:
    """空白压缩：HTML 里的换行/缩进/全角空格都不该影响判定。"""
    return " ".join((value or "").replace("\u3000", " ").split())


def _absolute_base(base_url: str) -> str:
    """给 urljoin 一个带协议的基址；空串或相对串补 http://。"""
    base = (base_url or "").strip()
    if base and "://" not in base:
        base = "http://" + base.lstrip("/")
    return base


def _host_key(url: str) -> str:
    """站点键：主机小写 + 去掉 www. 前缀；解析失败返回空串。"""
    target = (url or "").strip()
    if not target:
        return ""
    if "://" not in target:
        target = "http://" + target.lstrip("/")
    try:
        host = (urlsplit(target).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _path_of(url: str) -> str:
    """安全取 path；非法 URL 一律当空路径。"""
    try:
        return urlsplit((url or "").strip()).path
    except ValueError:
        return ""


def _url_ext(url: str) -> str:
    """URL 路径的最后一段后缀（小写、不带点）；无后缀返回空串。"""
    name = _path_of(url).rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _url_key(url: str) -> str:
    """去重键：复用 url_norm.url_key；极端异常时退回原文，绝不抛给调用方。"""
    try:
        return url_norm.url_key(url)
    except (TypeError, ValueError):
        return (url or "").strip()


def is_attachment(url: str) -> bool:
    """URL 是否指向附件：只看路径后缀是否属于 LINK_EXT（query 不参与）。"""
    if not url:
        return False
    return _url_ext(url) in LINK_EXT


def _usable_href(href: str) -> bool:
    """href 是否值得补全：排除空、纯锚点、javascript/mailto/tel/data 等伪协议。"""
    value = (href or "").strip()
    if not value or value.startswith("#"):
        return False
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return False
    return scheme in ("", "http", "https")


def _is_http_url(url: str) -> bool:
    """补全后只接受 http/https；其它协议一律不进池。"""
    try:
        scheme = urlsplit((url or "").strip()).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


class _AnchorParser(HTMLParser):
    """收集所有 <a href> 的原始信息；闭合/嵌套由 flush 兜住，坏 HTML 不丢链接。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self.flush()                                  # 上一个 <a> 没闭合：先收尾
        attrs_map = {key.lower(): (value or "") for key, value in attrs}
        self._current = {
            "href": attrs_map.get("href", "").strip(),
            "rel": attrs_map.get("rel", "").strip(),
            "text": [],
        }

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self.flush()

    def flush(self) -> None:
        """把当前 <a> 落成一条记录；无 href 的也收，交给上层过滤。"""
        if self._current is None:
            return
        text = _clean_text("".join(self._current["text"]))[:MAX_LINK_TEXT]
        self.links.append({
            "href": self._current["href"],
            "rel": self._current["rel"],
            "text": text,
        })
        self._current = None


def extract_links(html: str, base_url: str) -> list[dict[str, Any]]:
    """解析 <a href>，补全相对地址并去重。

    规则：
      - 相对 URL 用 urllib.parse.urljoin 按 base_url 补全；
      - 砍掉 #锚点；纯 "#xxx"、javascript:、mailto:、tel:、data: 直接丢；
      - 同一条 URL（按 url_norm 归一）只保留第一次出现，输出顺序 = 文档顺序；
      - 保留原 rel 与链接文字（空白压缩），is_attachment 由后缀判定。
    """
    parser = _AnchorParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:                                # 残缺 HTML 不阻断整页发现
        pass
    parser.flush()

    base = _absolute_base(base_url)
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in parser.links:
        href = raw["href"]
        if not _usable_href(href):
            continue
        try:
            absolute = urljoin(base, href)
        except ValueError:
            continue
        absolute = urldefrag(absolute)[0]            # 去锚点，保留 query
        if not _is_http_url(absolute):
            continue
        key = _url_key(absolute)
        if key in seen:
            continue
        seen.add(key)
        links.append({
            "url": absolute,
            "text": raw["text"],
            "rel": raw["rel"],
            "is_attachment": is_attachment(absolute),
        })
    return links


# ============================================================ ② 站点约束
def same_site(urls: list[Any], seed_url: str) -> list[Any]:
    """只保留与 seed 同站（host 相同，忽略 www.）的条目，顺序不变。

    urls 里的元素可以是 URL 字符串，也可以是带 "url" 键的 dict；
    返回元素的类型与输入一致，方便直接接 extract_links 的结果。
    """
    items = list(urls or [])
    seed_host = _host_key(seed_url)
    if not seed_host:                                # 种子本身无 host：没有过滤依据
        return items
    kept: list[Any] = []
    for item in items:
        raw = item.get("url", "") if isinstance(item, dict) else item
        if _host_key(str(raw or "")) == seed_host:
            kept.append(item)
    return kept


# ============================================================ ③ 页面分类
def _is_nav_text(text: str) -> bool:
    """整条链接文字是否只是导航词（去掉装饰符号后精确比对，避免误杀长标题）。"""
    cleaned = _NAV_TRIM_RE.sub("", text or "").lower()
    return bool(cleaned) and cleaned in NAV_WORDS


def _is_section_root(path: str) -> bool:
    """纯首页 / 栏目根路径："/"、以 "/" 结尾的目录、index/default 首页。"""
    value = (path or "/").lower()
    if value in ("", "/") or value.endswith("/"):
        return True
    return value.rsplit("/", 1)[-1] in _INDEX_NAMES


def _detail_score(url: str, text: str) -> int:
    """详情页可能性打分：命中 DETAIL_HINTS 越多越像；文字够长再加一分。"""
    lowered = (url or "").lower()
    score = sum(1 for hint in DETAIL_HINTS if hint in lowered)
    if len(text or "") >= 8:
        score += 1
    return score


def list_detail_links(html: str, base_url: str,
                      min_text: int = 2) -> list[dict[str, Any]]:
    """列表页 → 详情候选（只留同站、非附件、非导航、非栏目根）。

    返回 [{url, text, score}]，顺序 = 文档顺序（稳定）；
    score 只用于调用方排序/截断，本函数不按分数重排。
    """
    base_host = _host_key(base_url)
    base_path = _path_of(_absolute_base(base_url))
    details: list[dict[str, Any]] = []
    for link in extract_links(html, base_url):
        url, text = link["url"], link["text"]
        if link["is_attachment"]:
            continue
        if len(text) < min_text:
            continue
        if _is_nav_text(text):
            continue
        if _is_section_root(_path_of(url)):
            continue
        if base_host and _host_key(url) != base_host:
            continue
        if base_path and _path_of(url) == base_path:  # 列表页自我链接（含 ?isMobile 之类）
            continue
        details.append({"url": url, "text": text, "score": _detail_score(url, text)})
    return details


def _has_pagination_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in PAGINATION_HINTS)


def _has_page_number(url: str) -> bool:
    """URL query 里 page=N 且 N>=2（page=1 就是首页，不算下一页）。"""
    try:
        query = urlsplit(url).query
    except ValueError:
        return False
    match = _PAGE_QUERY_RE.search(query)
    return bool(match) and int(match.group(1)) >= 2


def next_pages(html: str, base_url: str) -> list[str]:
    """找出翻页链接（绝对 URL，去重，仅同站）。

    命中任一条件即算：rel 含 next、文字含翻页提示、query 里 page=N（N>=2）。
    """
    base_host = _host_key(base_url)
    pages: list[str] = []
    seen: set[str] = set()
    for link in extract_links(html, base_url):
        url = link["url"]
        rel_tokens = link["rel"].lower().split()
        hit = ("next" in rel_tokens
               or _has_pagination_text(link["text"])
               or _has_page_number(url))
        if not hit:
            continue
        if is_attachment(url):
            continue
        if base_host and _host_key(url) != base_host:
            continue
        key = _url_key(url)
        if key in seen:
            continue
        seen.add(key)
        pages.append(url)
    return pages


def attachments(html: str, base_url: str) -> list[dict[str, Any]]:
    """所有附件链接 → [{url, ext, text}]，ext 小写不带点，顺序 = 文档顺序。"""
    files: list[dict[str, Any]] = []
    for link in extract_links(html, base_url):
        if not link["is_attachment"]:
            continue
        files.append({
            "url": link["url"],
            "ext": _url_ext(link["url"]),
            "text": link["text"],
        })
    return files


# ============================================================ ④ 站点地图
def _split_name(tag: str) -> tuple[str, str]:
    """拆成 (命名空间前缀, 本地名)，全小写；sitemap:loc → ("sitemap", "loc")。"""
    prefix, _, local = (tag or "").rpartition(":")
    return prefix.lower(), local.lower()


def _local_name(tag: str) -> str:
    """命名空间无关：sitemap:loc / ns:loc 一律取冒号后的本地名。"""
    return _split_name(tag)[1]


def _dedupe(values: list[str]) -> list[str]:
    """保序去重（不依赖 set 遍历，输出确定）。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


class _SitemapParser(HTMLParser):
    """只认 url / sitemap 两个分组里的 loc；标签名后缀匹配，忽略命名空间。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)      # &amp; 在这里还原成 &
        self.root = ""
        self.stack: list[tuple[str, str]] = []
        self.group = ""
        self.buffer: list[str] | None = None
        self.urls: list[str] = []
        self.sitemaps: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        prefix, name = _split_name(tag)
        if not self.root:
            self.root = name
        if name in ("url", "sitemap"):
            self.group = name
        elif name == "loc":
            # 只收 url/sitemap 的直接子 loc；image:/video: 等扩展 loc 不是页面地址
            parent = self.stack[-1] if self.stack else ("", "")
            same_ns = parent[0] == prefix and parent[1] in ("url", "sitemap")
            self.buffer = [] if same_ns else None
        self.stack.append((prefix, name))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.buffer is not None:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        name = _local_name(tag)
        if name == "loc":
            self.flush_loc()
        elif name in ("url", "sitemap"):
            self.group = ""
        if self.stack:
            self.stack.pop()

    def flush_loc(self) -> None:
        """把 <loc> 文字落库；缺失分组时按根标签兜底。"""
        if self.buffer is None:
            return
        value = _clean_text("".join(self.buffer))
        self.buffer = None
        if not value:
            return
        to_sitemap = self.group == "sitemap" or (
            not self.group and self.root == "sitemapindex")
        (self.sitemaps if to_sitemap else self.urls).append(value)


def parse_sitemap(xml: str) -> dict[str, list[str]]:
    """解析 sitemap XML，同时支持 <urlset><url><loc> 与 <sitemapindex><sitemap><loc>。

    命名空间无关（按标签名后缀匹配）；loc 文本里的 &amp; 由 HTMLParser 还原；
    保序去重；解析异常时返回已经解析到的部分，绝不抛给调用方。
    """
    parser = _SitemapParser()
    try:
        parser.feed(xml or "")
        parser.close()
    except Exception:                                # 残缺 XML 不阻断已解析内容
        pass
    parser.flush_loc()
    return {"urls": _dedupe(parser.urls), "sitemaps": _dedupe(parser.sitemaps)}


def parse_robots_sitemaps(robots_text: str) -> list[str]:
    """从 robots.txt 文本里取所有 Sitemap: 行（大小写/前导空白不敏感，保序去重）。"""
    values: list[str] = []
    for raw_line in (robots_text or "").lstrip("\ufeff").splitlines():
        line = raw_line.split("#", 1)[0].strip()     # robots 注释从 # 开始
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() != "sitemap":
            continue
        value = value.strip()
        if value:
            values.append(value)
    return _dedupe(values)


# ============================================================ ⑤ 入池
def _pool_depth(item: Any, default: int) -> int:
    """取 dict 的 depth；缺省/非法/负数一律回落默认值。"""
    if not isinstance(item, dict):
        return default
    try:
        depth = int(item.get("depth", default))
    except (TypeError, ValueError):
        return default
    return depth if depth >= 0 else default


def _pool_item(item: Any, default_depth: int) -> dict[str, Any] | None:
    """把字符串或 dict 统一成池内条目；空 URL 返回 None。"""
    if isinstance(item, dict):
        url = str(item.get("url") or "").strip()
        source = str(item.get("source") or "")
        depth = _pool_depth(item, default_depth)
    else:
        url = str(item or "").strip()
        source = ""
        depth = default_depth
    if not url:
        return None
    return {"url": url, "source": source, "depth": depth}


def merge_pool(existing: list[Any], new: list[Any], limit: int) -> dict[str, Any]:
    """把新发现的 URL 并进已有池：去重、限深、限数、顺序稳定。

    入池顺序 = existing 原顺序 → new 原顺序（BFS 层序由调用方按层喂入保证）。
    - existing 里的字符串按 depth=0；new 里的字符串按「已有最大深度 + 1」；
    - 带 depth 的 dict 以自带值为准，超过 MAX_DEPTH 的丢掉；
    - 去重键用 url_norm（同 URL 不同写法只留第一次出现）；
    - 最终 pool 条数 ≤ limit，被拒的候选数记在 dropped。
    """
    existing_items = list(existing or [])
    new_items = list(new or [])
    next_depth = 0
    if existing_items:
        next_depth = max((_pool_depth(item, 0) for item in existing_items),
                         default=0) + 1

    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for item, default_depth in (
            [(item, 0) for item in existing_items]
            + [(item, next_depth) for item in new_items]):
        envelope = _pool_item(item, default_depth)
        if envelope is None:
            dropped += 1
            continue
        key = _url_key(envelope["url"])
        if key in seen or envelope["depth"] > MAX_DEPTH or len(pool) >= limit:
            dropped += 1
            continue
        seen.add(key)
        pool.append(envelope)
    return {"pool": pool, "dropped": dropped}


# ============================================================ ⑥ 自测
def _self_test() -> None:
    """零网络自检：链接补全/去伪协议、四类分类、sitemap、robots、入池。"""
    html = (
        "<html><body>"
        "<nav>"
        "<a href=\"/\">首页</a>"
        "<a href=\"/login\">登录</a>"
        "<a href=\"/about.html\">关于我们</a>"
        "<a href=\"javascript:void(0)\">展开</a>"
        "<a href=\"mailto:a@example.com\">邮箱</a>"
        "<a href=\"#top\">回到顶部</a>"
        "</nav>"
        "<div class=\"list\">"
        "<a href=\"../article/1001.html\" rel=\"nofollow\">凯里酸汤鱼产业规模持续扩大</a>"
        "<a href=\"/article/1002.html\">酸汤鱼餐饮门店数量统计</a>"
        "<a href=\"/article/1001.html\">重复链接</a>"
        "<a href=\"https://other.example.org/article/9.html\">外部转载</a>"
        "<a href=\"/files/report.pdf\">2025年产业报告（PDF）</a>"
        "<a href=\"/files/data.xlsx?v=2\">数据附表</a>"
        "</div>"
        "<div class=\"pager\">"
        "<a href=\"/news/index.html?page=1\">上一页</a>"
        "<a rel=\"next\" href=\"/news/index.html?page=2\">下一页</a>"
        "<a href=\"/news/index.html?page=8\">末页</a>"
        "</div>"
        "</body></html>")
    base = "https://www.example.com/news/index.html"

    links = extract_links(html, base)
    assert len(links) == 11, len(links)
    assert all(link["url"].startswith("https://") for link in links), links
    assert not any("javascript:" in link["url"] or "mailto:" in link["url"]
                   for link in links), links
    assert not any("#" in link["url"] for link in links), links
    first = next(link for link in links if link["url"].endswith("/article/1001.html"))
    assert first["rel"] == "nofollow", first
    assert first["url"] == "https://www.example.com/article/1001.html", first
    assert sum(1 for link in links if link["is_attachment"]) == 2, links
    assert is_attachment("https://www.example.com/a.PDF?x=1"), "is_attachment 大小写/query"

    details = list_detail_links(html, base)
    assert [item["url"] for item in details] == [
        "https://www.example.com/article/1001.html",
        "https://www.example.com/article/1002.html",
    ], details
    assert all(item["score"] > 0 for item in details), details

    pages = next_pages(html, base)
    assert pages == [
        "https://www.example.com/news/index.html?page=2",
        "https://www.example.com/news/index.html?page=8",
    ], pages

    files = attachments(html, base)
    assert [item["ext"] for item in files] == ["pdf", "xlsx"], files

    own = same_site(links, "http://example.com/")
    assert len(own) == len(links) - 1, own               # 只剩外部那条被滤掉
    assert same_site([{"url": "https://www.example.com/a"}], "https://example.com/")

    urlset = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
        "<url><loc>https://www.example.com/a.html?x=1&amp;y=2</loc></url>"
        "<url><loc>https://www.example.com/b.html</loc></url>"
        "</urlset>")
    parsed = parse_sitemap(urlset)
    assert parsed["urls"][0] == "https://www.example.com/a.html?x=1&y=2", parsed
    assert len(parsed["urls"]) == 2 and not parsed["sitemaps"], parsed

    index = (
        "<sitemapindex xmlns:sm=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
        "<sm:sitemap><sm:loc>https://www.example.com/sitemap-1.xml</sm:loc></sm:sitemap>"
        "</sitemapindex>")
    parsed = parse_sitemap(index)
    assert parsed["sitemaps"] == ["https://www.example.com/sitemap-1.xml"], parsed
    assert not parsed["urls"], parsed

    robots = ("User-agent: *\nDisallow: /private/\n"
              "Sitemap: https://www.example.com/sitemap.xml\n"
              "  sitemap: https://www.example.com/sitemap-news.xml\n")
    assert parse_robots_sitemaps(robots) == [
        "https://www.example.com/sitemap.xml",
        "https://www.example.com/sitemap-news.xml",
    ], parse_robots_sitemaps(robots)

    merged = merge_pool(
        [{"url": "https://www.example.com/", "source": "seed", "depth": 0}],
        [{"url": "https://www.example.com/a.html", "source": "list", "depth": 1},
         "https://www.example.com/b.html",
         {"url": "https://www.example.com/a.html", "source": "dup", "depth": 1},
         {"url": "https://www.example.com/deep.html", "source": "deep",
          "depth": MAX_DEPTH + 1}],
        limit=3)
    assert len(merged["pool"]) == 3, merged
    assert merged["dropped"] == 2, merged
    assert [item["depth"] for item in merged["pool"]] == [0, 1, 1], merged
    assert merged["pool"][2]["url"].endswith("/b.html"), merged   # 字符串按下一层补深度
    again = merge_pool(
        [{"url": "https://www.example.com/", "source": "seed", "depth": 0}],
        [{"url": "https://www.example.com/a.html", "source": "list", "depth": 1},
         "https://www.example.com/b.html",
         {"url": "https://www.example.com/a.html", "source": "dup", "depth": 1},
         {"url": "https://www.example.com/deep.html", "source": "deep",
          "depth": MAX_DEPTH + 1}],
        limit=3)
    assert merged == again, "同一输入两次调用必须完全一致"

    print("[OK] discover 自检通过：链接/详情/翻页/附件/sitemap/robots/入池（零网络）")


if __name__ == "__main__":
    _self_test()
