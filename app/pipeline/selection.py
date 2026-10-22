"""本地召回规则，只有纯文本运算，不加载模型或访问文件。

结构：aliases → retrieval_settings；numeric_hint → balanced_passages / supplement_numeric。
调用：workflow 显式聚合入口传入研究组件的词表和 BGE 分数，再调用本模块。
数据流：问题/配置/已验证段落 → 有效词表、均衡预算、可追溯补充召回。
返回：settings dict；段落 list；段落副本 list。词表只用于匹配，不替原文填写事实。
"""

import re
from collections import defaultdict, deque


def aliases(value):
    """统一配置词表；由检索配置构造及工作流的上下文抽取调用。"""
    if isinstance(value, dict):
        return {key: list(dict.fromkeys([key, *words])) for key, words in value.items()}
    return {word: [word] for word in value}


def retrieval_settings(question, config, default_subjects, default_regions, metrics):
    """空配置使用已有词表，自动补充问题中明确出现的产业别名和指标。"""
    subjects = aliases(config.subjects or default_subjects)
    regions = aliases(config.regions or default_regions)
    if not config.subjects and "餐饮业" in subjects:
        subjects["餐饮业"] = ["餐饮业", "餐饮", "餐饮行业", "餐饮服务业"]
    topic_terms = list(dict.fromkeys(word for words in subjects.values() for word in words
                                    if word in question))
    metric_terms = list(dict.fromkeys(word for words in metrics.values() for word in words
                                     if len(word) >= 3 and word in question))
    keywords = list(dict.fromkeys([*config.keywords, *topic_terms, *metric_terms]))
    # 指标名称补充召回只接受数值句；避免“收入”等泛词让整篇文章入选。
    numeric_metrics = list(dict.fromkeys(word for words in metrics.values() for word in words
                                        if len(word) >= 3))
    queries = list(dict.fromkeys([question, *[f"{word} 年度统计数据 规模 收入 产量"
                                            for word in topic_terms[:3]]]))
    return {"queries": queries, "keywords": keywords, "subjects": subjects, "regions": regions,
            "semantic_threshold": config.semantic_threshold, "topic_terms": topic_terms,
            "numeric_metrics": numeric_metrics,
            "scope_source": "explicit_config" if config.subjects or config.regions else "built_in_vocabulary"}


def numeric_hint(text, settings):
    """仅识别有指标名称及带单位数字的片段，用于预算排序与补查，不创造数值。"""
    return bool(re.search(r"\d[\d,.]*\s*(?:亿元|万元|元|万吨|吨|万家|家|万户|户|万亩|亩|[%％])", text)
                and any(word in text for word in settings["numeric_metrics"]))


def balanced_passages(passages, settings, limit):
    """按文档轮流分配预算，文档内优先指标数值，最后恢复原顺序与原偏移。"""
    if len(passages) <= limit:
        return passages
    groups = defaultdict(list)
    for index, passage in enumerate(passages):
        text, title = passage.get("text") or "", passage.get("title") or ""
        numeric = numeric_hint(text, settings)
        topic = any(word in text or word in title
                    for word in [*settings["topic_terms"], *settings["keywords"]])
        priority = 0 if numeric and topic else 1 if topic else 2 if numeric else 3
        key = passage.get("text_sha256") or passage.get("record_id") or passage.get("url")
        groups[key].append((priority, index))
    queues = deque(deque(index for _, index in sorted(group)) for group in groups.values())
    chosen = []
    while queues and len(chosen) < limit:
        queue = queues.popleft()
        chosen.append(queue.popleft())
        if queue:
            queues.append(queue)
    return [passages[index] for index in sorted(chosen)]


def supplement_numeric(scored, settings):
    """补查正文/标题与产业有关的指标数值；宽泛问题保留数值供口径检查。"""
    result = []
    topics = settings["topic_terms"] or settings["keywords"]
    for passage in scored:
        row = dict(passage)
        text, title = row.get("text") or "", row.get("title") or ""
        topical = not topics or any(word in text or word in title for word in topics)
        if (not row["selected"] and topical and numeric_hint(text, settings)
                and not row.get("semantic_exclusion_hits")):
            row.update(selected=True, semantic_only=False, relevance_reason="numeric_context")
        result.append(row)
    return result


def expand_numeric_neighbors(scored):
    """相关段落相邻的数值行补进候选，给表格行和省略指标的分句保留解释机会。"""
    groups = defaultdict(list)
    result = [dict(row) for row in scored]
    for index, row in enumerate(result):
        groups[row.get("text_sha256") or row.get("record_id")].append(index)
    for indices in groups.values():
        ordered = sorted(indices, key=lambda index: result[index].get("char_start", index))
        anchors = {position for position, index in enumerate(ordered) if result[index]["selected"]}
        for position, index in enumerate(ordered):
            row = result[index]
            if (not row["selected"] and not row.get("semantic_exclusion_hits")
                    and re.search(r"\d", row.get("text") or "")
                    and (position - 1 in anchors or position + 1 in anchors)):
                row.update(selected=True, semantic_only=False, relevance_reason="adjacent_numeric_context")
    return result
