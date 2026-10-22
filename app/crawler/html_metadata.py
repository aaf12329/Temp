"""HTML字段定位的纯补充提取器，不访问网络，不修改原始HTML或正文坐标。

结构：publication_date → MetadataParser → recover_metadata。
调用：IntegratedCrawler晋升原始HTML时调用，已有有效字段优先，缺失字段才补。
数据流：响应字节/声明编码/既有字段 → 定位到的发布时间和标题 + 可审计定位依据。
借鉴资料中的元素属性/容器定位；只接受明确发布标识，不把正文年度或更新时间当发布时间。
"""

import re
import unicodedata
from datetime import datetime
from html.parser import HTMLParser

DATE_KEYS = frozenset({"article:published_time", "og:published_time", "datepublished", "pubdate",
                       "publishdate", "publish_date", "pubtime", "dc.date.issued", "dcterms.issued"})
PUB_IDENTIFIERS = frozenset({"pubtime", "pub-time", "pub_time", "publish-time", "publish_time",
                            "publishdate", "publish-date", "published-date", "article-time"})
DATE_PATTERN = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})日?"
    r"(?:\s*[ T]\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?(?!\d)")
PUB_LABEL = re.compile(r"发布时间|发布日期|发文日期|发布于")
VOID_TAGS = frozenset({"meta", "link", "img", "br", "hr", "input", "source", "wbr", "area", "base"})


def publication_date(value):
    """仅规范化完整有效日期，支持历史年份及全角字符，不臆造月日。"""
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ].+)?", text):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.date().isoformat() if len(text) == 10 else parsed.isoformat()
        except ValueError:
            return None
    matches = list(DATE_PATTERN.finditer(text))
    if len(matches) != 1:
        return None
    try:
        y, m, d, h, minute, second = matches[0].groups()
        parsed = datetime(int(y), int(m), int(d), int(h or 0), int(minute or 0), int(second or 0))
        return parsed.isoformat() if h else parsed.date().isoformat()
    except ValueError:
        return None


class MetadataParser(HTMLParser):
    """只扫描明确元数据节点和有发布标签的短容器，不执行脚本。"""

    def __init__(self):
        """为一次HTML解析初始化有界栈、候选与标题；由recover_metadata构造。"""
        super().__init__(convert_charrefs=True)
        self.frames, self.dates, self.titles = [], [], []

    def _date(self, raw, selector):
        """记录明确节点的有效日期和原值；不记录无效或含更新标签的片段。"""
        if not isinstance(raw, str) or re.search(r"更新|修改|最后访问", raw):
            return
        normalized = publication_date(raw)
        if normalized and len(self.dates) < 64:
            self.dates.append({"value": normalized, "raw": raw[:600], "selector": selector})

    def handle_starttag(self, tag, attrs):
        """识别meta/itemprop与明确发布时间属性；只被HTMLParser调用。"""
        attributes = {name.lower(): value or "" for name, value in attrs}
        blocked = any(frame["blocked"] for frame in self.frames) or tag in {
            "script", "style", "nav", "footer", "noscript"}
        if tag == "meta":
            keys = {attributes.get(key, "").lower() for key in ("name", "property", "itemprop")}
            if not blocked and keys & DATE_KEYS:
                self._date(attributes.get("content"), "meta:" + sorted(keys & DATE_KEYS)[0])
        if tag in VOID_TAGS:
            return
        identifiers = set(re.split(r"\s+", attributes.get("class", "").lower()))
        identifiers.add(attributes.get("id", "").lower())
        explicit = (attributes.get("itemprop", "").lower() == "datepublished"
                    or bool(identifiers & PUB_IDENTIFIERS))
        if explicit and not blocked and attributes.get("datetime"):
            self._date(attributes["datetime"], tag + "[datetime]")
        self.frames.append({"tag": tag, "blocked": blocked, "explicit": explicit, "text": ""})

    def handle_startendtag(self, tag, attrs):
        """处理自闭合元素，防止无结束标签污染定位栈。"""
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        """为当前容器保留短文本；正文来源本身保持原样，不写回。"""
        if self.frames and self.frames[-1]["blocked"]:
            return
        for frame in self.frames:
            if not frame["blocked"] and len(frame["text"]) < 600:
                frame["text"] += data[:600 - len(frame["text"])]

    def handle_endtag(self, tag):
        """只在短容器中接纳发布标签日期；冲突留给汇总，不按先后猜值。"""
        matching = [i for i, frame in enumerate(self.frames) if frame["tag"] == tag]
        if not matching:
            return
        index = matching[-1]
        frame = self.frames[index]
        del self.frames[index:]
        text = frame["text"].strip()
        if frame["blocked"]:
            return
        if frame["explicit"]:
            self._date(text, tag + "[publication-marker]")
        if tag in {"p", "span", "div", "time"} and len(text) <= 200:
            label = PUB_LABEL.search(text)
            if label and not re.search(r"计划|预计|拟定", text[max(0, label.start() - 8):label.start()]):
                self._date(text[label.start():label.end() + 45], tag + "[publication-label]")
        if tag == "h1" and 2 <= len(text) <= 300 and len(self.titles) < 8:
            self.titles.append(text)


def recover_metadata(content, encoding, current_source, current_title):
    """从已保存字节恢复缺失字段，返回fields与audit；不会覆盖有效既有发布时间。"""
    decoded, used = None, None
    for codec in dict.fromkeys([encoding or "utf-8-sig", "utf-8-sig", "gb18030"]):
        try:
            decoded, used = content.decode(codec), codec
            break
        except (UnicodeDecodeError, LookupError):
            continue
    audit = {"method": "html_element_metadata", "encoding": used, "recovered": [], "issues": []}
    if decoded is None:
        audit["issues"].append("原始HTML编码无法可靠识别，未补字段")
        return {}, audit
    parser = MetadataParser()
    parser.feed(decoded)
    parser.close()
    fields = {}
    existing = publication_date(current_source.get("published_at"))
    if not existing:
        dates = {item["value"][:10] for item in parser.dates}
        if len(dates) == 1:
            # 不同时间精度的同一天，回退到共同明确的日期，避免凭空制造具体时刻。
            values = {item["value"] for item in parser.dates}
            fields["published_at"] = next(iter(values)) if len(values) == 1 else next(iter(dates))
            audit["recovered"].append({"field": "published_at", "value": fields["published_at"],
                                        "evidence": parser.dates})
        elif len(dates) > 1:
            audit["issues"].append("存在多个不同的明确发布日期，未自动选择")
    if not current_title:
        titles = set(parser.titles)
        if len(titles) == 1:
            fields["title"] = next(iter(titles))
            audit["recovered"].append({"field": "title", "value": fields["title"], "selector": "h1"})
    return fields, audit
