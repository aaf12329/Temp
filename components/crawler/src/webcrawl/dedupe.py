# -*- coding: utf-8 -*-
"""跨 URL 去重 —— 同一篇通稿被多家转载时只保留一份（零网络、零落盘）。

为什么需要：落盘按 URL 存文件，`content_sha256` 只记录不拦截，
同一篇通稿被 5 家转载就是 5 份"不同"正文；下游按行统计会重复计数，
训练集与测试集之间还会互相泄漏（AGENTS.md §4「划分维度 = 防泄漏维度」）。

本模块是**纯函数**：只吃 dict 列表、只吐 dict。
- 零网络、零落盘、不 import 项目内其它模块（可单独 `python dedupe.py` 跑自测）
- 🔴 **不依赖 `is_reprint` 判重**（实测 13 条里 5 条被判转载，判定偏松）；
  `is_reprint` 只在 `choose_keeper` 里当排序参考
- 不用 Python 内置 `hash()`（跨进程随机）；指纹用 hashlib.blake2b / sha1
- 输出全部排序稳定：同一输入两次调用逐字段一致

================================ 结构树 =================================
① 归一化与指纹（纯计算，零 I/O）
    normalize_text(text)          全角→半角、小写、去空白与标点，只留字母/数字/汉字
    shingles(text, k=5)           归一化后的 k 字窗口集合（标点差异不影响）
    fingerprint(text, k=5, size=64)
                                  {"simhash": int, "shingle_hash": [int...], "length": int}
② 相似度
    similarity(a_fp, b_fp)        shingle 集合 Jaccard（主）；无 shingle 时 simhash 兜底
    is_duplicate(a_fp, b_fp, threshold=0.85)   similarity >= threshold
③ 去重与归并（吃记录列表）
    find_duplicates(records, *, threshold=0.85, text_key="text")
                                  {"groups": [...], "duplicate_count": int, "skipped": int}
    choose_keeper(group)          组内保留优先级：首发时间 > 权威发布 > is_reprint=False
                                  > 正文更长 > 字典序
    group_by_canonical(records)   canonical_url 相同的归一组（转载归并线索）
    content_key(record)           sha256 优先，否则文本指纹
④ 自测
    _self_test()                  3 篇同稿不同站 + 1 篇无关 + 100 条 × 5000 字性能

================================ 调用关系 ================================
（待接入）main.py / extract 层 ──▶ find_duplicates() / group_by_canonical()
    └─ 只读 record dict，不写任何文件；落盘由调用方经 io_utils 统一出口

================================ 对外返回契约 ==============================
find_duplicates(records) -> {
    "groups": [{"keep": record_id,
                "drops": [{"record_id": str, "similarity": float, "reason": "sha256"|"text"}]}],
    "duplicate_count": int,     # 被丢弃（重复）的记录条数 = Σ len(drops)
    "skipped": int,             # 缺 record_id / 缺 text / 空正文 / 非 dict，跳过不抛异常
}
content_sha256 相同直接判重（similarity=1.0，reason="sha256"）；其余按文本相似度
（reason="text"）。groups 按 keep 排序、drops 按 record_id 排序，输出与输入顺序无关。
"""

from __future__ import annotations

import array
import hashlib
import math
import random
import re
import sys
import time
import unicodedata
from typing import Any, Iterable

# ---------------------------------------------------------------- 常量区
DEFAULT_K = 5                       # 5 字 shingle：1055~5429 字正文下区分度足够
DEFAULT_SIZE = 64                   # simhash 位宽（支持 1..64）
DEFAULT_THRESHOLD = 0.85            # Jaccard 判重阈值
MAX_SIMHASH_BITS = 64               # shingle 哈希固定 8 字节，simhash 最多取 64 位
SIMHASH_MARGIN_MIN = 4              # 汉明距离预筛的固定余量
SIMHASH_GATE_MIN_SHINGLES = 128     # shingle 太少时 simhash 方差大，预筛直接放行做精确比对

# 日期抽取：2026-09-07 / 2026/9/7 / 2026年9月7日，可带时分秒
DATE_RE = re.compile(
    r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})日?"
    r"(?:\s*[ T]\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?")

# 归一化时清掉的"非词字符"：标点、空白、符号、下划线（汉字/字母/数字都算 \w）
_NOISE_RE = re.compile(r"[\W_]+")

__all__ = [
    "normalize_text", "shingles", "fingerprint", "similarity", "is_duplicate",
    "find_duplicates", "choose_keeper", "group_by_canonical", "content_key",
]


# ==================== ① 归一化与指纹（纯计算，零 I/O） ====================


def normalize_text(text: str) -> str:
    """归一化：全角→半角（NFKC）、小写、去掉空白与标点噪声，只留字母/数字/汉字。

    各站转载会在标点、全角半角、空白上做手脚；归一化后这些差异全部消失，
    指纹只对"信息本身"敏感。非字符串输入返回空串（不抛异常）。

    用 `unicodedata.normalize("NFKC")` + C 级正则而不是逐字符 Python 循环：
    5000 字正文从 ~7.6 ms 降到 ~0.3 ms，100 条性能预算主要省在这里。
    """
    if not isinstance(text, str):
        return ""
    return _NOISE_RE.sub("", unicodedata.normalize("NFKC", text)).lower()


def shingles(text: str, k: int = DEFAULT_K) -> set[str]:
    """归一化后取 k 字窗口集合（标点/空白差异不影响指纹）。

    正文短于 k 时返回整串（短文本仍可比）；空文本返回空集合。
    """
    if k < 1:
        raise ValueError("k 必须 >= 1")
    return _shingles_of_normalized(normalize_text(text), k)


def _shingles_of_normalized(normalized: str, k: int) -> set[str]:
    """归一化串 → k 字窗口集合（内部复用，避免二次归一化）。"""
    if not normalized:
        return set()
    if len(normalized) < k:
        return {normalized}
    return {normalized[index:index + k] for index in range(len(normalized) - k + 1)}


def _stable_hash(value: str) -> int:
    """稳定 64 位哈希：blake2b，跨进程一致（🔴 不用内置 hash()）。"""
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _byte_offsets(normalized: str) -> list[int]:
    """字符偏移表：每个字符的 UTF-8 起始字节位置（末尾多一个总长度）。"""
    offsets = [0]
    position = 0
    for char in normalized:
        code = ord(char)
        if code < 0x80:
            position += 1
        elif code < 0x800:
            position += 2
        elif code < 0x10000:
            position += 3
        else:
            position += 4
        offsets.append(position)
    return offsets


def _shingle_hashes(normalized: str, k: int) -> list[int]:
    """归一化串 → 排序去重后的 64 位 shingle 哈希列表（确定性）。

    先整串 encode 一次，再按字节偏移切窗口：避免每个窗口都 encode 一遍。
    """
    if not normalized:
        return []
    if len(normalized) < k:
        return [_stable_hash(normalized)]
    encoded = normalized.encode("utf-8")
    offsets = _byte_offsets(normalized)
    digest = hashlib.blake2b
    from_bytes = int.from_bytes
    hashes = {
        from_bytes(digest(encoded[start:end], digest_size=8).digest(), "big")
        for start, end in zip(offsets, offsets[k:])
    }
    return sorted(hashes)


def _simhash(hashes: list[int], size: int) -> int:
    """全权重 simhash：每个 shingle 哈希的每一位等权投票。

    按"字节列"统计而不是逐位构造 8 倍长的掩码：把 n 个 8 字节哈希按字节列
    切成 8 条 n 字节长串，每列的 8 个位用 `int.bit_count` 一次数清。
    得票过半则该位为 1（平票记 0，保证确定性）。

    🔴 必须用**全量** shingle 投票：按排序位置抽样会让"少量替换"改变采样对齐，
    相似稿的汉明距离被假性放大（实测 0.87 Jaccard 的稿子曾算出 21 位），预筛会漏判。
    """
    if not hashes:
        return 0
    word = 8                                    # 每个 shingle 哈希固定 8 字节（64 位）
    # array 一次成型（C 级），避免 5000 次 to_bytes；字节序统一成小端再按列切
    words = array.array("Q", hashes)
    if words.itemsize == word:
        if sys.byteorder != "little":
            words.byteswap()
        packed = words.tobytes()
    else:                                       # 极端平台兜底：手工拼小端字节
        packed = b"".join(value.to_bytes(word, "little") for value in hashes)
    count = len(hashes)
    result = 0
    masks: dict[int, int] = {}                  # 掩码只依赖 count 与字节内位号，复用 8 次
    for byte_index in range(word):
        column = int.from_bytes(packed[byte_index::word], "big")
        for bit_in_byte in range(8):
            bit = byte_index * 8 + bit_in_byte          # 位号，0 = 最低位
            if bit >= size:
                continue
            mask = masks.get(bit_in_byte)
            if mask is None:
                mask = int.from_bytes(bytes([1 << bit_in_byte]) * count, "big")
                masks[bit_in_byte] = mask
            ones = (column & mask).bit_count()
            if 2 * ones > count:                # 过半为 1；平票记 0
                result |= 1 << bit
    return result


def _fingerprint_normalized(normalized: str, k: int, size: int) -> dict[str, Any]:
    """归一化串 → 指纹（内部复用，避免二次归一化）。"""
    hash_list = _shingle_hashes(normalized, k)
    return {
        "simhash": _simhash(hash_list, size),
        "shingle_hash": hash_list,
        "length": len(normalized),
    }


def fingerprint(text: str, k: int = DEFAULT_K, size: int = DEFAULT_SIZE) -> dict[str, Any]:
    """正文 → 指纹 {"simhash": int, "shingle_hash": [int...], "length": int}。

    - shingle_hash：排序去重的 64 位哈希列表，供 Jaccard 精确比对
    - simhash：位宽 size 的近似指纹，供 `find_duplicates` 的预筛加速
    - length：归一化后的字符数
    """
    if not (1 <= size <= MAX_SIMHASH_BITS):
        raise ValueError(f"size 必须在 1..{MAX_SIMHASH_BITS} 之间")
    if k < 1:
        raise ValueError("k 必须 >= 1")
    return _fingerprint_normalized(normalize_text(text), k, size)


# ==================== ② 相似度 ====================


def _shingle_set(fp: dict[str, Any]) -> set[int]:
    """从指纹里取 shingle 哈希集合（容错：缺键/类型不对都当空）。"""
    raw = fp.get("shingle_hash")
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {int(value) for value in raw if isinstance(value, int)}


def _int_or_zero(value: Any) -> int:
    return int(value) if isinstance(value, int) else 0


def _simhash_similarity(a_fp: dict[str, Any], b_fp: dict[str, Any]) -> float:
    """simhash 汉明距离 → [0,1] 相似度（仅在缺 shingle 列表时兜底）。"""
    a_value = _int_or_zero(a_fp.get("simhash"))
    b_value = _int_or_zero(b_fp.get("simhash"))
    hamming = (a_value ^ b_value).bit_count()
    return round(1.0 - hamming / DEFAULT_SIZE, 6)


def _jaccard(a_set: set[int], b_set: set[int]) -> float:
    """shingle 集合 Jaccard；空集合的语义在调用方决定，这里给确定值。"""
    if not a_set and not b_set:
        return 1.0
    if not a_set or not b_set:
        return 0.0
    inter = len(a_set & b_set)
    union = len(a_set) + len(b_set) - inter
    return round(inter / union, 6) if union else 0.0


def similarity(a_fp: dict[str, Any], b_fp: dict[str, Any]) -> float:
    """两个指纹的相似度：以 shingle 集合 Jaccard 为主，simhash 兜底。

    - 双方都有 shingle：Jaccard = |交集| / |并集|
    - 双方都为空指纹：返回 1.0（都是空正文）；只有一方为空：0.0
    - 指纹只带 simhash 没有 shingle 列表（外部粗粒度指纹）：用汉明距离兜底
    """
    if not isinstance(a_fp, dict) or not isinstance(b_fp, dict):
        return 0.0
    a_set = _shingle_set(a_fp)
    b_set = _shingle_set(b_fp)
    if not a_set and not b_set:
        a_len = _int_or_zero(a_fp.get("length"))
        b_len = _int_or_zero(b_fp.get("length"))
        if a_len == 0 and b_len == 0:
            return 1.0
        return _simhash_similarity(a_fp, b_fp)
    if not a_set or not b_set:
        return 0.0
    return _jaccard(a_set, b_set)


def is_duplicate(a_fp: dict[str, Any], b_fp: dict[str, Any],
                 threshold: float = DEFAULT_THRESHOLD) -> bool:
    """相似度达到阈值即判重（阈值含等号）。"""
    return similarity(a_fp, b_fp) >= threshold


# ==================== ③ 去重与归并（吃记录列表） ====================


def _record_id_of(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    value = record.get("record_id")
    return value.strip() if isinstance(value, str) else ""


def _source_of(record: dict[str, Any]) -> dict[str, Any]:
    source = record.get("source")
    return source if isinstance(source, dict) else {}


def _published_key(raw: Any) -> tuple[int, int, int, int, int, int, int]:
    """发布时间 → 可比较的整数元组；缺失/不可解析排最后（保证确定性）。"""
    if not isinstance(raw, str) or not raw.strip():
        return (1, 9999, 99, 99, 99, 99, 99)
    match = DATE_RE.search(raw.strip())
    if not match:
        return (1, 9999, 99, 99, 99, 99, 99)
    year, month, day, hour, minute, second = match.groups()
    return (0, int(year), int(month), int(day),
            int(hour or 0), int(minute or 0), int(second or 0))


def _text_of(record: dict[str, Any], text_key: str) -> str:
    value = record.get(text_key)
    return value if isinstance(value, str) else ""


def _keeper_key(record: dict[str, Any], text_key: str) -> tuple[Any, ...]:
    """choose_keeper 的排序键（越小越优先），顺序即需求给的优先级。"""
    source = _source_of(record)
    publisher = source.get("publisher")
    publisher_text = publisher.strip() if isinstance(publisher, str) else ""
    if publisher_text and "转载" not in publisher_text:
        authority = 0                           # 权威发布：有 publisher 且不是转载
    elif publisher_text:
        authority = 2                           # 自述"转载/转载自"
    else:
        authority = 1                           # 抽不到 publisher：居中
    reprint = source.get("is_reprint")
    reprint_rank = 0 if reprint is False else (2 if reprint is True else 1)
    return (
        _published_key(source.get("published_at")),   # 1) 首发更早
        authority,                                    # 2) 权威发布
        reprint_rank,                                 # 3) is_reprint=False
        -len(_text_of(record, text_key)),             # 4) 正文更长
        _record_id_of(record),                        # 5) 字典序兜底
    )


def choose_keeper(group: Iterable[dict[str, Any]],
                  *, text_key: str = "text") -> str | None:
    """组内保留哪一条：首发更早 > 权威发布 > is_reprint=False > 正文更长 > 字典序。

    只做排序，不判重——判重永远只看正文相似度 / content_sha256。
    空组返回 None。
    """
    candidates = [record for record in (group or []) if _record_id_of(record)]
    if not candidates:
        return None
    keeper = min(candidates, key=lambda record: _keeper_key(record, text_key))
    return _record_id_of(keeper)


def _canonical_of(record: dict[str, Any]) -> str:
    value = _source_of(record).get("canonical_url")
    if not isinstance(value, str) or not value.strip():
        value = record.get("canonical_url")
    return value.strip() if isinstance(value, str) else ""


def group_by_canonical(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """按 canonical_url 归并：同 canonical 且 ≥2 条成组，其余进 orphans。

    `canonical_url` 是站点自述的"原文地址"，是转载归并的**线索**而非判据；
    正文是否重复仍以 find_duplicates 为准。只输出排序稳定的列表。
    """
    buckets: dict[str, dict[str, None]] = {}
    orphans: set[str] = set()
    for record in records or []:
        record_id = _record_id_of(record)
        if not record_id:
            continue
        canonical = _canonical_of(record)
        if canonical:
            buckets.setdefault(canonical, {})[record_id] = None
        else:
            orphans.add(record_id)
    groups: list[dict[str, Any]] = []
    for canonical in sorted(buckets):
        record_ids = sorted(buckets[canonical])
        if len(record_ids) >= 2:
            groups.append({"canonical_url": canonical, "record_ids": record_ids})
        else:
            orphans.update(record_ids)
    return {"groups": groups, "orphans": sorted(orphans)}


def content_key(record: dict[str, Any]) -> str:
    """内容键：content_sha256 优先，否则归一化正文的 sha1 指纹。

    缺 sha256 且缺正文时返回空串（调用方据此跳过）。
    """
    if not isinstance(record, dict):
        return ""
    sha = record.get("content_sha256")
    if isinstance(sha, str) and sha.strip():
        return "sha256:" + sha.strip()
    text = record.get("text")
    if not isinstance(text, str):
        return ""
    normalized = normalize_text(text)
    if not normalized:
        return ""
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return "text:" + digest


def _collect_entries(records: Iterable[dict[str, Any]], text_key: str,
                     k: int, size: int) -> tuple[list[dict[str, Any]], int]:
    """把记录列表变成可比对的条目；缺字段的一律跳过并计数（不抛异常）。"""
    entries: list[dict[str, Any]] = []
    skipped = 0
    for record in records or []:
        record_id = _record_id_of(record)
        text = _text_of(record, text_key) if isinstance(record, dict) else ""
        if not record_id or not text:
            skipped += 1
            continue
        normalized = normalize_text(text)
        if not normalized:
            skipped += 1
            continue
        fp = _fingerprint_normalized(normalized, k, size)
        sha = record.get("content_sha256")
        entries.append({
            "record": record,
            "record_id": record_id,
            "sha": sha.strip() if isinstance(sha, str) and sha.strip() else "",
            "fingerprint": fp,
            "shingles": set(fp["shingle_hash"]),
            "length": fp["length"],
        })
    return entries, skipped


def _find_root(parent: list[int], index: int) -> int:
    while parent[index] != index:
        parent[index] = parent[parent[index]]      # 路径压缩
        index = parent[index]
    return index


def _union(parent: list[int], left: int, right: int) -> None:
    left_root = _find_root(parent, left)
    right_root = _find_root(parent, right)
    if left_root != right_root:
        parent[right_root] = left_root


def _hamming_gate(threshold: float, size: int) -> int:
    """Jaccard 阈值 → simhash 汉明距离的**宽松**上限（预筛，不参与最终判定）。

    Jaccard=t 时集合余弦约 2t/(1+t)，simhash 期望汉明比例 = arccos(余弦)/π。
    在期望值上再加 max(4, size/8) 位余量，只滤掉明显不可能的对，宁可多算也不漏判。
    """
    clamped = min(max(threshold, 0.0), 0.999)
    cosine = 2.0 * clamped / (1.0 + clamped)
    ratio = math.acos(cosine) / math.pi
    margin = max(SIMHASH_MARGIN_MIN, size // 8)
    return int(math.ceil(ratio * size)) + margin


def _candidate(a: dict[str, Any], b: dict[str, Any],
               threshold: float, gate: int) -> bool:
    """廉价预筛：长度比（数学上界）+ simhash 汉明距离（留足余量）。

    长度比是严格上界；simhash 只是**加速**，门限按阈值推出来再加余量，
    shingle 太少时（短文本）方差大，直接放行交给精确 Jaccard 判。
    """
    smaller = min(a["length"], b["length"])
    larger = max(a["length"], b["length"])
    if larger <= 0:
        return False
    # 数学上 Jaccard <= min/max；留 1e-6 容差防浮点边界误杀
    if smaller / larger < threshold - 1e-6:
        return False
    if min(len(a["shingles"]), len(b["shingles"])) < SIMHASH_GATE_MIN_SHINGLES:
        return True
    hamming = (a["fingerprint"]["simhash"] ^ b["fingerprint"]["simhash"]).bit_count()
    return hamming <= gate


def _compare(keeper: dict[str, Any], other: dict[str, Any]) -> tuple[float, str]:
    """丢弃项相对保留项的相似度与原因；sha256 相同按需求记 1.0。"""
    if keeper["sha"] and other["sha"] and keeper["sha"] == other["sha"]:
        return 1.0, "sha256"
    return _jaccard(keeper["shingles"], other["shingles"]), "text"


def find_duplicates(records: Iterable[dict[str, Any]], *,
                    threshold: float = DEFAULT_THRESHOLD,
                    text_key: str = "text") -> dict[str, Any]:
    """跨 URL 去重：同稿只留一份，返回分组、保留者、丢弃者与跳过数。

    判定：content_sha256 相同 → 直接判重（similarity=1.0，reason="sha256"）；
    其余按归一化正文的 shingle Jaccard（reason="text"）。
    先做长度比 + simhash 汉明预筛，再对候选算精确 Jaccard，避免 O(n²) 全文比对。
    分组取相似关系的**连通分量**（A~B、B~C 会把 C 也归进同一组），组内按
    choose_keeper 选保留者；drops 报告的是相对保留者的相似度。
    """
    entries, skipped = _collect_entries(records, text_key, DEFAULT_K, DEFAULT_SIZE)
    total = len(entries)
    parent = list(range(total))
    gate = _hamming_gate(threshold, DEFAULT_SIZE)

    # ① content_sha256 相同直接连边（最硬的证据）
    first_by_sha: dict[str, int] = {}
    for index, entry in enumerate(entries):
        if not entry["sha"]:
            continue
        if entry["sha"] in first_by_sha:
            _union(parent, first_by_sha[entry["sha"]], index)
        else:
            first_by_sha[entry["sha"]] = index

    # ② 正文相似度：预筛后精确比对；已同组的不必再比（不影响连通分量）
    for left in range(total):
        for right in range(left + 1, total):
            if _find_root(parent, left) == _find_root(parent, right):
                continue
            if not _candidate(entries[left], entries[right], threshold, gate):
                continue
            if _jaccard(entries[left]["shingles"], entries[right]["shingles"]) >= threshold:
                _union(parent, left, right)

    components: dict[int, list[dict[str, Any]]] = {}
    for index, entry in enumerate(entries):
        components.setdefault(_find_root(parent, index), []).append(entry)

    groups: list[dict[str, Any]] = []
    for members in components.values():
        if len(members) < 2:
            continue
        keeper_id = choose_keeper([member["record"] for member in members], text_key=text_key)
        keeper = next(member for member in members if member["record_id"] == keeper_id)
        drops: list[dict[str, Any]] = []
        for member in sorted((item for item in members if item is not keeper),
                             key=lambda item: item["record_id"]):
            score, reason = _compare(keeper, member)
            drops.append({"record_id": member["record_id"],
                          "similarity": score, "reason": reason})
        groups.append({"keep": keeper_id, "drops": drops})
    groups.sort(key=lambda group: group["keep"])

    return {
        "groups": groups,
        "duplicate_count": sum(len(group["drops"]) for group in groups),
        "skipped": skipped,
    }


# ==================== ④ 自测（零网络） ====================


def _make_text(length: int, seed: int) -> str:
    """确定性地造中文测试正文（同一 seed 永远同一串）。"""
    rng = random.Random(seed)
    pool = ("酸汤鱼产业调查数据贵阳市黔东南州凯里餐饮收入企业数量产值增速同比增长"
            "稻花鱼木姜子市场消费游客接待量万元亿元规模以上限额以上同比环比占比"
            "贵州省统计局人民政府门户网站新闻报道记者编辑责任编辑来源转载原标题")
    return "".join(rng.choice(pool) for _ in range(length))


def _self_test() -> None:
    """零网络自检：同稿只留一篇 / keeper 是首发 / 输出确定 / 100 条性能。"""
    base = _make_text(1800, 7)
    records: list[dict[str, Any]] = [
        {"record_id": "a-origin", "url": "https://a.cn/1", "content_sha256": "sha-a",
         "text": base,
         "source": {"publisher": "新华社", "published_at": "2026-09-07T10:00:00",
                    "is_reprint": False, "canonical_url": None}},
        {"record_id": "b-reprint", "url": "https://b.cn/2", "content_sha256": "sha-b",
         "text": base + "本文转载自新华网。",
         "source": {"publisher": "转载自新华社", "published_at": "2026-09-08T10:00:00",
                    "is_reprint": True, "canonical_url": None}},
        {"record_id": "c-reprint", "url": "https://c.cn/3", "content_sha256": "sha-c",
         "text": "（贵阳讯）" + base,
         "source": {"publisher": None, "published_at": "2026-09-09T10:00:00",
                    "is_reprint": None, "canonical_url": None}},
        {"record_id": "d-unrelated", "url": "https://d.cn/4", "content_sha256": "sha-d",
         "text": _make_text(1800, 99),
         "source": {"publisher": "贵阳日报", "published_at": "2026-09-06T10:00:00",
                    "is_reprint": False, "canonical_url": None}},
    ]
    result = find_duplicates(records, threshold=0.85)
    assert result["duplicate_count"] == 2, result
    assert len(result["groups"]) == 1, result
    group = result["groups"][0]
    assert group["keep"] == "a-origin", group          # 首发 > 权威 > 时间晚的转载
    assert [drop["record_id"] for drop in group["drops"]] == ["b-reprint", "c-reprint"], group
    assert all(drop["reason"] == "text" for drop in group["drops"]), group
    assert all(drop["similarity"] >= 0.85 for drop in group["drops"]), group

    shuffled = list(records)
    random.Random(3).shuffle(shuffled)
    assert find_duplicates(shuffled, threshold=0.85) == result, "输入顺序影响输出"
    assert find_duplicates(records, threshold=0.85) == result, "两次结果不一致"

    perf_records = [
        {"record_id": f"r{index:03d}", "text": _make_text(5000, 1000 + index),
         "source": {}}
        for index in range(100)
    ]
    started = time.perf_counter()
    perf_result = find_duplicates(perf_records, threshold=0.85)
    elapsed = time.perf_counter() - started
    assert perf_result["duplicate_count"] == 0, perf_result
    assert perf_result["skipped"] == 0, perf_result
    assert elapsed < 2.0, f"100 条 × 5000 字耗时 {elapsed:.2f}s，超过 2s 上限"

    print("[OK] dedupe 自检通过：3 篇同稿只留 1 篇 / keeper=首发 / 输出确定 / "
          f"100 条×5000 字 {elapsed * 1000:.0f} ms（零网络）")


if __name__ == "__main__":
    _self_test()
