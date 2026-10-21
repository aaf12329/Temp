# -*- coding: utf-8 -*-
"""来源陈述元数据 —— 在 HTML 里找「谁说的 / 什么时候说的 / 原文在哪」。

《爬虫标准API接口.md》v1.2 §4.2：`source.{publisher, published_at, canonical_url,
author, declaration, is_reprint}` 是「来源陈述」的骨架，缺了下游无法回答
「这条说法是谁在什么时候说的」，整条证据链降级为"网上看到过"。

🔴 本模块只做**抽取**，不做猜测：抽不到就是 None，**绝不允许编**。
🔴 `is_reprint` 是三态：True=跨主体转载 / False=本站自己的稿子 / None=判不了。
   声明是 JS 模板残留、HTML 实体、正文残句，或站点自身身份无从确认时一律 None，
   **宁可 None，不许猜 True**。

=============================== 结构与调用 ================================
① 纯逻辑：抽取（只吃 HTML 字符串，零网络）
    find_canonical(html)       <link rel=canonical>
    find_author(html)          meta author / article:author
    find_published(html)       meta 时间 / JSON-LD datePublished / 正文日期
    find_publisher(html)       og:site_name / article:publisher / 页脚声明
    find_declaration(html)     「来源：XXX」原句
    normalize_date(raw)        各种日期写法 → ISO8601（不可解析返回原串）
② 纯逻辑：归一并判定（同样零网络）
    clean_declaration(text)    原句 → 干净机构名；垃圾返回 None
    clean_publisher(text)      发布主体去「来源：」前缀与逗号拼接残渣
    org_core(name)             剥省/市/州/县/人民政府/门户网… 通用后缀
    same_org(a, b)             归一后相等、或短的是长的前缀 → 同一主体
③ 汇总
    extract(html, site, final_url, title=None) -> dict   §4.2 的 source 对象
④ 自测
    _self_test()               零网络

=============================== 调用关系 ================================
api.py（组装 CrawlResult 时）──▶ extract()
extract() ──▶ html_utils.parse_title（取 <title> 里的站点自称）
          ──▶ clean_declaration / clean_publisher / same_org

=============================== 判定规则 ================================
有声明：清洗 → 垃圾 None；声明「转载自/转自」→ True；
        与（publisher ∪ <title> 站点名段 ∪ 域名标签）任一 same_org → False；
        有可比对象但都不像 → True；站点自身身份一个都取不到 → None。
无声明：canonical 与 final_url 不同 → True，相同 → False，缺失 → None。
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from webcrawl import html_utils
except ImportError:                                  # 直接运行本文件（自测入口）
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from webcrawl import html_utils

# ---------------------------------------------------------------- 正则区
CANONICAL_RE = re.compile(
    r"<link[^>]+rel=[\"']?canonical[\"']?[^>]*href=[\"']([^\"']+)[\"']", re.I)
CANONICAL_RE2 = re.compile(
    r"<link[^>]+href=[\"']([^\"']+)[\"'][^>]*rel=[\"']?canonical[\"']?", re.I)

META_RE = re.compile(r"<meta\b[^>]*>", re.I)
ATTR_RE = re.compile(r"([a-zA-Z:_-]+)\s*=\s*[\"']([^\"']*)[\"']")

DATE_KEYS = (
    "article:published_time", "og:published_time", "pubdate", "publishdate",
    "publish_date", "date", "dc.date", "dc.date.issued", "sailthru.date",
    "weibo:article:create_at", "og:release_date", "release_date",
)
AUTHOR_KEYS = ("author", "article:author", "og:article:author", "dc.creator")
PUBLISHER_KEYS = (
    "og:site_name", "article:publisher", "publisher", "dc.publisher",
    "mediaid", "application-name", "sitename",
)
JSONLD_DATE_RE = re.compile(r"\"datePublished\"\s*:\s*\"([^\"]+)\"", re.I)
JSONLD_PUB_RE = re.compile(r"\"publisher\"\s*:\s*\{[^}]*\"name\"\s*:\s*\"([^\"]+)\"", re.I | re.S)

DECL_RES = (
    re.compile(r"来源[：:]\s*([^\s|｜,，。；;\n]{2,30})"),
    re.compile(r"稿源[：:]\s*([^\s|｜,，。；;\n]{2,30})"),
    re.compile(r"转载自[：:]?\s*([^\s|｜,，。；;\n]{2,30})"),
)

# 2026-09-30 / 2026/9/30 / 2026年9月30日 / 2026-09-30 10:20(:30)
DATE_PAT = re.compile(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})日?"
                      r"(?:\s*[ T]\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?")

# ---------------------------------------------------------------- 归一区
# 机构类型词：清洗后的声明至少命中一个，才算「像机构名」（挡掉「主要包括」这类正文残句）
ORG_HINTS = (
    "政府", "人民", "委员会", "办公厅", "办公室", "局", "厅", "部", "委", "办",
    "院", "校", "所", "站", "社", "报", "网", "台", "中心", "公司", "集团",
    "协会", "学会", "大学", "频道", "客户端", "新闻", "出版", "传媒", "银行",
    "官网", "网站", "门户", "系统", "融媒", "广电", "日报", "晚报", "商报",
    "在线", "人大", "政协",
)
# 通用后缀：从名字**末尾**反复剥掉；长的先剥，避免「人民政府」被拆成「人民」+「政府」
GENERIC_SUFFIXES = tuple(sorted((
    "自治州", "自治县", "人民政府", "广播电视台", "融媒体中心", "新闻中心",
    "门户网站", "有限公司", "办公厅", "办公室", "门户网", "新闻网", "日报社",
    "电视台", "客户端", "网站", "日报", "晚报", "商报", "政府", "中心",
    "传媒", "集团", "股份", "官方", "门户", "省", "市", "州", "县", "区",
    "镇", "乡", "网", "报", "台", "办",
), key=len, reverse=True))
# 剥完只剩通用词的「核心」没有比较价值（否则「中国」会前缀命中所有「中国*」）
GENERIC_ONLY = frozenset({"中国", "人民", "政府", "省", "市", "州", "县", "区",
                          "镇", "乡", "网", "报", "台", "办", "中心", "官方"})
# 「短名是长名前缀」时，长名多出来的部分只允许是民族/自治限定词或下级行政区，
# 否则「黔东南新闻网」会被「黔东南苗族侗族自治州农业科学院」的前缀误判为同一主体
QUALIFIER_CHARS = frozenset("族自治苗侗布依彝土家仡佬水回白瑶壮满蒙藏维朝鲜")
ADMIN_MARKERS = frozenset("省市区县州镇乡")

# 代码残留特征：JS 模板字符串 / 拼接收尾 / 页面模板残片
JUNK_RE = re.compile(r"['\"`<>{}]|\.\.\.|\+|=|;|&|%|\.\.|SourceName")
JUNK_WORDS = ("SourceName", "wzly", "document", "window", "function",
              "var ", "console", "undefined", "null")

# 「来源：XXX」标签；转载类标签本身就是明确的转载证据
LABEL_RE = re.compile(r"^\s*(?:文章来源|来源|稿源|转引自|转载自|转自)\s*[：:]?\s*", re.S)
REPRINT_LABEL_RE = re.compile(r"^\s*(?:转引自|转载自|转自)")

# <title> 里分隔「标题 | 站点名」的记号（标题-站点名 是最常见的写法）
TITLE_SEP_RE = re.compile(r"\s*[_|｜]\s*|\s*[—–]\s*|\s+-\s*|\s*-\s*")
WSP_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[\s\u3000·、，,。.；;：:！!？?“”\"'‘’()（）\[\]【】<>《》—–\-|/\\]+")
DOMAIN_STOP = frozenset({"www", "com", "gov", "org", "net", "edu", "html", "htm"})


# ============================================ ① 纯逻辑：抽取
def _meta_pairs(html: str) -> dict[str, str]:
    """把所有 <meta> 收成 {key 小写: content}。key 取 name/property/http-equiv。"""
    pairs: dict[str, str] = {}
    for tag in META_RE.findall(html):
        attrs = {k.lower(): v for k, v in ATTR_RE.findall(tag)}
        content = attrs.get("content")
        if content is None:
            continue
        for key in ("property", "name", "http-equiv", "itemprop"):
            name = attrs.get(key)
            if name:
                pairs.setdefault(name.strip().lower(), content.strip())
    return pairs


def normalize_date(raw: str | None) -> str | None:
    """日期写法归一成 ISO8601。已经带 T/时区的原样返回；解析不了返回原串。"""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}T", text):
        return text
    match = DATE_PAT.search(text)
    if not match:
        return text
    year, month, day = match.group(1), match.group(2).zfill(2), match.group(3).zfill(2)
    hour, minute, second = match.group(4), match.group(5), match.group(6)
    if hour:
        clock = f"T{hour.zfill(2)}:{minute}:{second or '00'}"
    else:
        clock = ""
    return f"{year}-{month}-{day}{clock}"


def find_canonical(html: str) -> str | None:
    """取 <link rel=canonical> 的 href；没有就 None。"""
    for pattern in (CANONICAL_RE, CANONICAL_RE2):
        match = pattern.search(html)
        if match:
            return match.group(1).strip() or None
    return None


def find_author(html: str) -> str | None:
    """取 meta 里的作者字段；没有就 None。"""
    pairs = _meta_pairs(html)
    for key in AUTHOR_KEYS:
        if pairs.get(key):
            return pairs[key]
    return None


def find_published(html: str) -> str | None:
    """取发布时间（meta / JSON-LD / 正文日期），归一成 ISO8601；没有就 None。"""
    pairs = _meta_pairs(html)
    for key in DATE_KEYS:
        value = pairs.get(key)
        if value:
            normalized = normalize_date(value)
            if normalized:
                return normalized
    match = JSONLD_DATE_RE.search(html)
    if match:
        return normalize_date(match.group(1))
    return None


def find_publisher(html: str) -> str | None:
    """取发布主体（og:site_name / article:publisher / 页脚声明）；没有就 None。"""
    pairs = _meta_pairs(html)
    for key in PUBLISHER_KEYS:
        if pairs.get(key):
            return pairs[key]
    match = JSONLD_PUB_RE.search(html)
    if match:
        return match.group(1).strip() or None
    return None


def find_declaration(html: str) -> str | None:
    """正文里「来源：XXX」这类自述，返回整句片段（保留原话）。"""
    text = re.sub(r"<[^>]+>", " ", html)
    for pattern in DECL_RES:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


# ============================================ ② 纯逻辑：归一与判定
def org_core(name: str | None) -> str:
    """去掉通用后缀与标点空白，返回可比较的机构核心字串。

    例：`黔东南州人民政府`→`黔东南`；`黔东南苗族侗族自治州人民政府门户网`
    →`黔东南苗族侗族`；`杭州网`→`杭州`；`中国食品报`→`中国食品`（不剥成`食品`，
    否则会和任意「食品*网」误判同一主体）。
    """
    if not name:
        return ""
    core = PUNCT_RE.sub("", html_lib.unescape(name)).lower()
    changed = True
    while changed and core:
        changed = False
        for token in GENERIC_SUFFIXES:
            if not core.endswith(token):
                continue
            # 「贵州」是省级专名，不是「贵」+「州」行政区后缀
            if token == "州" and core.endswith("贵州"):
                continue
            remain = len(core) - len(token)
            # 单字后缀（省/市/州/网/报…）只在核心还剩 ≥2 字时剥，
            # 否则「杭州」会被剥成「杭」、「麻江县」会剩一个「麻」。
            if remain < 2 and len(token) == 1:
                continue
            core = core[:remain]
            changed = True
            break
    if core.startswith("中国") and len(core) - 2 >= 3:
        core = core[2:]
        for token in GENERIC_SUFFIXES:
            if core.endswith(token) and len(core) - len(token) >= 2:
                core = core[: -len(token)]
                break
    return "" if core in GENERIC_ONLY else core


def same_org(a: str | None, b: str | None) -> bool:
    """两个名字归一后相等、或短的是长的前缀 → 同一主体。

    `黔东南州人民政府` vs `黔东南苗族侗族自治州人民政府门户网` → True（前缀+民族限定词）；
    `麻江县农业农村局` vs `麻江县人民政府网` → True（前缀+下级行政区）；
    `中央广电总台国际在线` vs `国际在线` → False（母品牌全称 vs 站点自称，不是前缀）。
    """
    ca, cb = org_core(a), org_core(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    short, long = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    if not long.startswith(short):
        return False
    extra = long[len(short):]
    if all(char in QUALIFIER_CHARS for char in extra):
        return True
    return extra[0] in ADMIN_MARKERS


def _looks_like_org(text: str) -> bool:
    """清洗后的字串像不像机构名：有 CJK/字母、长度 ≥2、含机构类型词或英文品牌。"""
    core = PUNCT_RE.sub("", text)
    if len(core) < 2:
        return False
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", core):
        return False
    if re.search(r"[A-Za-z]{2,}", core):
        return True
    return any(hint in core for hint in ORG_HINTS)


def clean_declaration(text: str | None) -> str | None:
    """「来源：XXX」原句 → 干净机构名；垃圾/判不了返回 None。

    处理：去 HTML 实体（&nbsp;）、去 JS 模板残留（`'+SourceName)` / `"+str)`）、
    去括号不配对的模板残片、空白与换行归一；清洗后不像机构名（无 CJK/字母、
    长度 <2、纯符号、正文残句如「主要包括」）→ None。
    """
    if not text:
        return None
    body = LABEL_RE.sub("", html_lib.unescape(text), count=1)
    if JUNK_RE.search(body) or any(word in body for word in JUNK_WORDS):
        return None
    if body.count("(") != body.count(")") or body.count("（") != body.count("）"):
        return None
    body = WSP_RE.sub(" ", body).strip().strip("：: ")
    if not body or not _looks_like_org(body):
        return None
    return body


def clean_publisher(text: str | None) -> str | None:
    """发布主体清洗：去「来源：」前缀与「中国,」这类逗号拼接残渣；取不到返回 None。"""
    if not text:
        return None
    body = WSP_RE.sub(" ", html_lib.unescape(text)).strip()
    body = LABEL_RE.sub("", body, count=1).strip()
    for piece in re.split(r"[,，;；]", body):
        piece = piece.strip()
        if not piece or piece in ("中国", "来源", "稿源", "转载自", "转自"):
            continue
        if len(PUNCT_RE.sub("", piece)) >= 2 and re.search(r"[A-Za-z\u4e00-\u9fff]", piece):
            return piece
    return None


def _title_identity(title: str | None) -> str | None:
    """从 <title> 末尾的站点名段（`_新浪网` / `-国际在线` / `|瓮安新闻网`）取站点自称。"""
    if not title:
        return None
    matches = list(TITLE_SEP_RE.finditer(title))
    if not matches:
        return None
    suffix = title[matches[-1].end():].strip()
    if not 2 <= len(suffix) <= 24:
        return None
    if re.search(r"[，。！？；、,;]", suffix):
        return None
    return suffix if _looks_like_org(suffix) else None


def _domain_matches(declared: str, site: str | None) -> bool:
    """声明里是否出现站点域名的可辨识标签（meituan.com → 「美团Meituan」）。"""
    if not site:
        return False
    low = declared.lower()
    for label in re.split(r"[.\-]", site.lower()):
        if len(label) >= 4 and label not in DOMAIN_STOP and label in low:
            return True
    return False


def _is_reprint_label(declaration: str | None) -> bool:
    """原句以「转载自 / 转自 / 转引自」开头 = 页面自己承认转载。"""
    return bool(declaration and REPRINT_LABEL_RE.search(html_lib.unescape(declaration)))


def _decide_reprint(declaration: str | None, publisher: str | None, title: str | None,
                    site: str | None, canonical: str | None,
                    final_url: str | None) -> tuple[bool | None, str]:
    """is_reprint 三态判定，返回 (值, 依据)。依据只用于自测/复算，不写进契约字段。"""
    pub = clean_publisher(publisher)
    if declaration:
        declared = clean_declaration(declaration)
        if not declared:
            return None, "junk-declaration"
        if len(org_core(declared)) < 2:
            return None, "declaration-unresolvable"
        candidates = [c for c in (pub, _title_identity(title)) if c]
        if any(same_org(declared, c) for c in candidates):
            return False, "same-org"
        if _domain_matches(declared, site):
            return False, "same-domain"
        if _is_reprint_label(declaration):
            return True, "reprint-label"
        if candidates:
            return True, "different-org"
        return None, "site-identity-unknown"
    if canonical and final_url:
        return (canonical.rstrip("/") != final_url.rstrip("/")), "canonical-vs-final"
    return None, "no-evidence"


# ============================================ ③ 汇总
def extract(html: str, site: str, final_url: str | None = None,
            title: str | None = None) -> dict[str, Any]:
    """组装契约 §4.2 的 `source` 对象。抽不到一律 None（is_reprint 允许 None）。

    `title` 可选：调用方已有 <title> 时传入可省一次解析；不传则本函数自己取。
    """
    canonical = find_canonical(html)
    declaration = find_declaration(html)
    publisher = find_publisher(html)
    page_title = title if title is not None else html_utils.parse_title(html)
    is_reprint, _ = _decide_reprint(declaration, publisher, page_title, site,
                                    canonical, final_url)

    return {
        "publisher": clean_publisher(publisher),
        "published_at": find_published(html),
        "canonical_url": canonical,
        "author": find_author(html),
        "declaration": declaration,
        "is_reprint": is_reprint,
    }


# ============================================ ④ 自测
def _self_test() -> None:
    """零网络自检：元信息抽取、日期归一、声明清洗与转载三态判定。"""
    samples = {
        "canonical": '<link rel="canonical" href="https://a.cn/a.html">',
        "published": '<meta property="article:published_time" content="2026-09-30T10:20:00+08:00">',
        "date_cn": "<span>发布时间：2026年9月30日 10:20</span>",
        "author": '<meta name="author" content="记者 张三">',
        "publisher": '<meta property="og:site_name" content="贵阳市统计局">',
        "declaration": "<p>来源：新华社</p>",
    }
    html = "<html><head>{}</head><body>{}</body></html>".format(
        samples["canonical"] + samples["published"] + samples["author"] + samples["publisher"],
        samples["declaration"])

    result = extract(html, "tjj.guiyang.gov.cn", "https://tjj.guiyang.gov.cn/")
    assert result["canonical_url"] == "https://a.cn/a.html", result
    assert result["published_at"].startswith("2026-09-30"), result
    assert result["author"] == "记者 张三", result
    assert result["publisher"] == "贵阳市统计局", result
    assert result["declaration"] and "新华社" in result["declaration"], result
    assert result["is_reprint"] is True, result        # 声明来源与本站不同 → 转载

    assert normalize_date("2026/9/30") == "2026-09-30"
    assert normalize_date("2026年9月30日") == "2026-09-30"
    assert normalize_date("2026-09-30 10:20") == "2026-09-30T10:20:00"
    assert normalize_date("不是日期") == "不是日期"     # 解析不了原样返回

    # 声明清洗：JS 模板残留 / HTML 实体 / 空白 → 垃圾 None 或归一后的机构名
    assert clean_declaration("来源：'+SourceName)") is None
    assert clean_declaration("来源：&nbsp") is None
    assert clean_declaration("来源：    主要包括") is None
    assert clean_declaration("来源： \n\t\t 贵州日报天眼新闻") == "贵州日报天眼新闻"

    # 机构归一：同主体不同全称 / 不同主体
    assert org_core("黔东南州人民政府") == "黔东南"
    assert same_org("黔东南州人民政府", "黔东南苗族侗族自治州人民政府门户网") is True
    assert same_org("中国食品报", "食品机械设备网") is False
    assert same_org("中央广电总台国际在线", "国际在线") is False

    # publisher 脏值清洗
    assert clean_publisher("中国,贵州省人民政府") == "贵州省人民政府"
    assert clean_publisher("中国,") is None

    # 有声明但要判断「是不是本站自己的稿子」时，站点身份取不到 → None（不许猜 True）
    unknown = extract("<html><body><p>来源：贵州日报天眼新闻</p></body></html>",
                      "dcpp.gog.cn", "http://dcpp.gog.cn/a.shtml")
    assert unknown["is_reprint"] is None, unknown

    # 无声明 → 回 canonical 对比
    diff = extract('<link rel="canonical" href="https://a.cn/x.html">',
                   "a.cn", "https://a.cn/y.html")
    assert diff["is_reprint"] is True, diff

    empty = extract("<html><body>无元信息</body></html>", "a.cn", "https://a.cn/")
    assert all(empty[k] is None for k in
               ("publisher", "published_at", "canonical_url", "author")), empty
    print("[OK] source_meta 自检通过：canonical/时间/作者/发布主体/转载三态判定（零网络）")


if __name__ == "__main__":
    _self_test()
