# -*- coding: utf-8 -*-
"""跨 URL 去重（dedupe）的纯逻辑测试 —— 零网络、零真实数据写入。

运行（仓库根目录）：
    .\\myvenv\\Scripts\\python.exe -m unittest tests.test_dedupe -v

覆盖：
    ① normalize_text   全角半角 / 空白 / 标点 / 大小写 / 非字符串容错 / 幂等
    ② shingles         k 字窗口、归一化先行、短文本、空文本、非法 k
    ③ fingerprint      输出契约、稳定哈希（独立 blake2b 复算）、确定性、simhash 位宽
    ④ similarity       Jaccard 精确值、空指纹语义、simhash 兜底、对称性
    ⑤ is_duplicate     阈值边界（含等号）
    ⑥ choose_keeper    首发 > 权威 > is_reprint > 更长 > 字典序，与组内顺序无关
    ⑦ find_duplicates  跨 URL 同稿归并、sha256 直判、缺字段跳过、阈值、可复现性
    ⑧ group_by_canonical / content_key
    ⑨ 性能：100 条 × 5000 字 find_duplicates < 2 秒
"""

import ast
import hashlib
import os
import random
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from webcrawl import dedupe                                    # noqa: E402

POOL = ("酸汤鱼产业调查数据贵阳市黔东南州凯里餐饮收入企业数量产值增速同比增长"
        "稻花鱼木姜子市场消费游客接待量万元亿元规模以上限额以上同比环比占比"
        "贵州省统计局人民政府门户网站新闻报道记者编辑责任编辑来源转载原标题")


def make_text(length: int, seed: int = 0) -> str:
    """确定性地造中文正文（同一 seed 永远同一串）。"""
    rng = random.Random(seed)
    return "".join(rng.choice(POOL) for _ in range(length))


def unique_text(length: int, start: int = 0x4E00) -> str:
    """每个字都不同的中文串：窗口互不重复，便于精确算 Jaccard。"""
    return "".join(chr(start + index) for index in range(length))


def fake_fp(hashes=(), simhash: int = 0, length: int = 0) -> dict:
    """手工构造指纹（用于精确验证 Jaccard / 兜底分支）。"""
    return {"shingle_hash": sorted(hashes), "simhash": simhash, "length": length}


def record(record_id, text=None, *, sha=None, published=None, publisher=None,
           is_reprint=None, canonical=None, url=None) -> dict:
    """按 CrawlResult 的形状（source 嵌套）造一条记录。"""
    return {
        "record_id": record_id,
        "url": url or f"https://example.cn/{record_id}",
        "content_sha256": sha,
        "text": text,
        "source": {"publisher": publisher, "published_at": published,
                   "is_reprint": is_reprint, "canonical_url": canonical},
    }


class TestNormalizeText(unittest.TestCase):
    """归一化：全角转半角、去空白/标点、大小写折叠。"""

    def test_full_width_to_half_width(self):
        self.assertEqual(dedupe.normalize_text("ＡＢＣ１２３"), "abc123")

    def test_full_width_punctuation_removed(self):
        self.assertEqual(dedupe.normalize_text("酸汤鱼！好吃？"), "酸汤鱼好吃")

    def test_whitespace_removed(self):
        self.assertEqual(dedupe.normalize_text("酸 汤\n鱼\t产 业"), "酸汤鱼产业")

    def test_lowercase(self):
        self.assertEqual(dedupe.normalize_text("ABC abc"), "abcabc")

    def test_digits_and_cjk_kept(self):
        self.assertEqual(dedupe.normalize_text("2026年产值412.68亿元"),
                         "2026年产值41268亿元")

    def test_non_string_returns_empty(self):
        self.assertEqual(dedupe.normalize_text(None), "")
        self.assertEqual(dedupe.normalize_text(123), "")
        self.assertEqual(dedupe.normalize_text(b"abc"), "")

    def test_idempotent(self):
        text = "酸汤鱼，2026 年！"
        once = dedupe.normalize_text(text)
        self.assertEqual(dedupe.normalize_text(once), once)

    def test_empty(self):
        self.assertEqual(dedupe.normalize_text(""), "")
        self.assertEqual(dedupe.normalize_text("，。！？\n\t"), "")


class TestShingles(unittest.TestCase):
    """shingle：归一化后的 k 字窗口集合。"""

    def test_basic_k3(self):
        self.assertEqual(dedupe.shingles("abcdef", 3),
                         {"abc", "bcd", "cde", "def"})

    def test_normalized_first(self):
        left = dedupe.shingles("酸汤鱼，好吃", 3)
        right = dedupe.shingles("酸汤鱼好吃", 3)
        self.assertEqual(left, right)

    def test_short_text_is_single_shingle(self):
        self.assertEqual(dedupe.shingles("ab", 5), {"ab"})

    def test_empty_text(self):
        self.assertEqual(dedupe.shingles(""), set())
        self.assertEqual(dedupe.shingles("，。！"), set())

    def test_invalid_k(self):
        with self.assertRaises(ValueError):
            dedupe.shingles("abc", 0)

    def test_window_count(self):
        text = unique_text(5000)
        self.assertEqual(len(dedupe.shingles(text, 5)), 5000 - 5 + 1)

    def test_returns_set_of_str(self):
        result = dedupe.shingles("酸汤鱼产业", 2)
        self.assertIsInstance(result, set)
        self.assertTrue(all(isinstance(item, str) for item in result))


class TestFingerprint(unittest.TestCase):
    """指纹契约：键齐全、稳定哈希、排序去重、simhash 位宽。"""

    def test_keys_exactly(self):
        self.assertEqual(set(dedupe.fingerprint("酸汤鱼产业")),
                         {"simhash", "shingle_hash", "length"})

    def test_length_is_normalized_length(self):
        # "酸汤鱼，好！" 去掉标点后是 4 个字：length 统计的是归一化后的字符数
        self.assertEqual(dedupe.fingerprint("酸汤鱼，好！")["length"], 4)

    def test_shingle_hash_sorted_unique_ints(self):
        result = dedupe.fingerprint(make_text(300, 1), k=4)
        hashes = result["shingle_hash"]
        self.assertTrue(hashes)
        self.assertEqual(hashes, sorted(hashes))
        self.assertEqual(len(hashes), len(set(hashes)))
        self.assertTrue(all(isinstance(value, int) for value in hashes))

    def test_deterministic(self):
        text = make_text(500, 2)
        self.assertEqual(dedupe.fingerprint(text), dedupe.fingerprint(text))

    def test_stable_hash_is_blake2b_not_builtin(self):
        """独立用 blake2b 复算，钉死"跨进程稳定"这条契约。"""
        text = "酸汤鱼"
        expected = int.from_bytes(
            hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "big")
        self.assertEqual(dedupe.fingerprint(text)["shingle_hash"], [expected])

    def test_fingerprint_is_stable_across_processes(self):
        """换一个 Python 进程重算，指纹必须逐位一致（内置 hash() 会当场露馅）。"""
        code = (
            "import sys; sys.path.insert(0, %r);"
            "from webcrawl import dedupe;"
            "print(dedupe.fingerprint('跨进程稳定性').__repr__())"
        ) % str(REPO_ROOT / "src")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ast.literal_eval(result.stdout.strip()),
                         dedupe.fingerprint("跨进程稳定性"))

    def test_simhash_width(self):
        text = make_text(200, 3)
        for size in (8, 32, 64):
            value = dedupe.fingerprint(text, size=size)["simhash"]
            self.assertGreaterEqual(value, 0)
            self.assertLess(value, 1 << size)
            self.assertLessEqual(value.bit_length(), size)

    def test_size_validation(self):
        for size in (0, 65):
            with self.assertRaises(ValueError):
                dedupe.fingerprint("abc", size=size)

    def test_k_validation(self):
        with self.assertRaises(ValueError):
            dedupe.fingerprint("abc", k=0)

    def test_empty_text(self):
        result = dedupe.fingerprint("")
        self.assertEqual(result, {"simhash": 0, "shingle_hash": [], "length": 0})

    def test_similar_texts_have_close_simhash(self):
        base = unique_text(600)
        changed = base[:300] + "替换" + base[302:]
        left = dedupe.fingerprint(base)
        right = dedupe.fingerprint(changed)
        hamming = (left["simhash"] ^ right["simhash"]).bit_count()
        self.assertLessEqual(hamming, 6)             # 近似稿汉明距离应当很小

    def test_unrelated_texts_have_far_simhash(self):
        left = dedupe.fingerprint(unique_text(600, 0x4E00))
        right = dedupe.fingerprint(unique_text(600, 0x8000))
        hamming = (left["simhash"] ^ right["simhash"]).bit_count()
        self.assertGreater(hamming, 10)


class TestSimilarity(unittest.TestCase):
    """相似度：Jaccard 为主，空指纹与 simhash 兜底语义明确。"""

    def test_identical_is_one(self):
        text = make_text(400, 4)
        self.assertEqual(dedupe.similarity(dedupe.fingerprint(text),
                                           dedupe.fingerprint(text)), 1.0)

    def test_jaccard_exact_value(self):
        left = fake_fp([1, 2, 3, 4])
        right = fake_fp([2, 3, 4, 5])
        self.assertEqual(dedupe.similarity(left, right), 0.6)   # 3 / 5

    def test_disjoint_is_zero(self):
        self.assertEqual(dedupe.similarity(fake_fp([1, 2]), fake_fp([3, 4])), 0.0)

    def test_symmetric(self):
        left, right = fake_fp([1, 2, 3]), fake_fp([3, 4])
        self.assertEqual(dedupe.similarity(left, right),
                         dedupe.similarity(right, left))

    def test_both_empty_fingerprints(self):
        self.assertEqual(dedupe.similarity(dedupe.fingerprint(""),
                                           dedupe.fingerprint("")), 1.0)

    def test_one_empty_fingerprint(self):
        self.assertEqual(dedupe.similarity(dedupe.fingerprint(""),
                                           dedupe.fingerprint("有正文")), 0.0)

    def test_simhash_fallback_without_shingles(self):
        left = {"simhash": 0, "length": 100}
        right = {"simhash": (1 << 8) - 1, "length": 100}
        self.assertEqual(dedupe.similarity(left, right), 1.0 - 8 / 64)

    def test_bad_input_returns_zero(self):
        self.assertEqual(dedupe.similarity(None, None), 0.0)
        self.assertEqual(dedupe.similarity({}, {"shingle_hash": [1]}), 0.0)
        self.assertEqual(dedupe.similarity("x", {}), 0.0)


class TestIsDuplicate(unittest.TestCase):
    """阈值判定：含等号，边界不许飘。"""

    def test_boundary_inclusive(self):
        left = fake_fp(range(1, 11))            # 10 个
        right = fake_fp(range(1, 7))            # 6 个 → J = 6/10 = 0.6
        self.assertEqual(dedupe.similarity(left, right), 0.6)
        self.assertTrue(dedupe.is_duplicate(left, right, 0.6))
        self.assertFalse(dedupe.is_duplicate(left, right, 0.61))
        self.assertTrue(dedupe.is_duplicate(left, right, 0.5))

    def test_identical_and_unrelated(self):
        text = make_text(300, 5)
        self.assertTrue(dedupe.is_duplicate(dedupe.fingerprint(text),
                                            dedupe.fingerprint(text), 1.0))
        self.assertFalse(dedupe.is_duplicate(dedupe.fingerprint(unique_text(300, 0x4E00)),
                                             dedupe.fingerprint(unique_text(300, 0x9000))))


class TestChooseKeeper(unittest.TestCase):
    """保留优先级：首发 > 权威 > is_reprint=False > 更长 > 字典序。"""

    def test_earliest_published_wins(self):
        early = record("early", "短", published="2026-09-01",
                       publisher=None, is_reprint=True)
        late = record("late", "更长的正文" * 50, published="2026-09-02",
                      publisher="新华社", is_reprint=False)
        self.assertEqual(dedupe.choose_keeper([late, early]), "early")

    def test_authority_wins_when_same_time(self):
        reprint = record("reprint", "正文", published="2026-09-01",
                         publisher="转载自新华社", is_reprint=False)
        origin = record("origin", "正文", published="2026-09-01",
                        publisher="新华社", is_reprint=True)
        self.assertEqual(dedupe.choose_keeper([reprint, origin]), "origin")

    def test_unknown_publisher_beats_declared_reprint(self):
        unknown = record("unknown", "正文", published="2026-09-01", publisher=None)
        declared = record("declared", "正文", published="2026-09-01",
                          publisher="转载自人民网")
        self.assertEqual(dedupe.choose_keeper([declared, unknown]), "unknown")

    def test_is_reprint_false_wins(self):
        flagged = record("flagged", "正文", published="2026-09-01",
                         publisher="新华社", is_reprint=True)
        clean = record("clean", "正文", published="2026-09-01",
                       publisher="新华社", is_reprint=False)
        self.assertEqual(dedupe.choose_keeper([flagged, clean]), "clean")

    def test_longer_text_wins(self):
        short = record("short", "短", published="2026-09-01", publisher="新华社",
                       is_reprint=False)
        long = record("long", "长正文" * 10, published="2026-09-01", publisher="新华社",
                      is_reprint=False)
        self.assertEqual(dedupe.choose_keeper([short, long]), "long")

    def test_lexicographic_tiebreak(self):
        left = record("aaa", "正文", published="2026-09-01", publisher="新华社",
                      is_reprint=False)
        right = record("bbb", "正文", published="2026-09-01", publisher="新华社",
                       is_reprint=False)
        self.assertEqual(dedupe.choose_keeper([right, left]), "aaa")

    def test_order_independent(self):
        group = [
            record("a", "正文", published=None, publisher="转载自新华社"),
            record("b", "正文", published="2026-09-01", publisher=None),
            record("c", "正文", published="2026/9/1", publisher="新华社",
                   is_reprint=False),
        ]
        self.assertEqual(dedupe.choose_keeper(group), dedupe.choose_keeper(group[::-1]))

    def test_empty_group(self):
        self.assertIsNone(dedupe.choose_keeper([]))
        self.assertIsNone(dedupe.choose_keeper(None))
        self.assertIsNone(dedupe.choose_keeper([{"url": "x"}]))     # 缺 record_id

    def test_missing_source_is_tolerated(self):
        group = [{"record_id": "no-source", "text": "正文"},
                 {"record_id": "empty-source", "text": "正文", "source": None}]
        self.assertEqual(dedupe.choose_keeper(group), "empty-source")

    def test_date_formats_are_comparable(self):
        slash = record("slash", "正文", published="2026/9/7", publisher="新华社")
        iso = record("iso", "正文", published="2026-09-08", publisher="新华社")
        self.assertEqual(dedupe.choose_keeper([iso, slash]), "slash")


class TestFindDuplicates(unittest.TestCase):
    """去重主流程：跨 URL 同稿只留一份，缺字段跳过不抛异常。"""

    def _trio(self):
        base = make_text(1500, 6)
        return base, [
            record("origin", base, published="2026-09-07T10:00:00",
                   publisher="新华社", is_reprint=False, url="https://a.cn/1"),
            record("reprint-b", base + "本文转载自新华网。", published="2026-09-08",
                   publisher="转载自新华社", is_reprint=True, url="https://b.cn/2"),
            record("reprint-c", "（贵阳讯）" + base, published="2026-09-09",
                   publisher=None, is_reprint=None, sha="different-sha",
                   url="https://c.cn/3"),
        ]

    def test_three_reprints_collapse_to_one(self):
        _, records = self._trio()
        result = dedupe.find_duplicates(records + [record("unrelated",
                                                          make_text(1500, 99))])
        self.assertEqual(result["duplicate_count"], 2)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(len(result["groups"]), 1)
        group = result["groups"][0]
        self.assertEqual(group["keep"], "origin")
        self.assertEqual([drop["record_id"] for drop in group["drops"]],
                         ["reprint-b", "reprint-c"])
        for drop in group["drops"]:
            self.assertEqual(drop["reason"], "text")
            self.assertAlmostEqual(drop["similarity"], 1.0, delta=0.02)

    def test_dedupe_ignores_is_reprint(self):
        base = make_text(1200, 7)
        records = [record("a", base, is_reprint=True),
                   record("b", base, is_reprint=False),
                   record("c", base, is_reprint=None)]
        result = dedupe.find_duplicates(records)
        self.assertEqual(result["duplicate_count"], 2)
        self.assertEqual(len(result["groups"]), 1)

    def test_same_sha256_is_duplicate_even_if_text_differs(self):
        left = record("left", make_text(600, 8), sha="same")
        right = record("right", unique_text(600), sha="same")
        result = dedupe.find_duplicates([left, right])
        self.assertEqual(result["duplicate_count"], 1)
        drop = result["groups"][0]["drops"][0]
        self.assertEqual(drop["reason"], "sha256")
        self.assertEqual(drop["similarity"], 1.0)

    def test_missing_fields_are_skipped(self):
        valid = [record("v1", "同一篇正文" * 100), record("v2", "同一篇正文" * 100)]
        broken = [
            {"text": "缺 record_id"},                           # 缺 record_id
            {"record_id": "no-text"},                           # 缺 text
            {"record_id": "none-text", "text": None},           # text 非字符串
            {"record_id": "empty-text", "text": ""},            # 空正文
            {"record_id": "punct-only", "text": "，。！\n\t"},     # 归一化后为空
            "not-a-dict",                                       # 非 dict
        ]
        result = dedupe.find_duplicates(valid + broken)
        self.assertEqual(result["skipped"], len(broken))
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(len(result["groups"]), 1)
        reported = {drop["record_id"] for drop in result["groups"][0]["drops"]}
        self.assertEqual(reported, {"v2"})

    def test_cross_url_is_the_whole_point(self):
        text = make_text(800, 9)
        records = [record("one", text, url="https://a.cn/a"),
                   record("two", text, url="https://b.cn/b"),
                   record("three", text, url="https://c.cn/c")]
        result = dedupe.find_duplicates(records)
        self.assertEqual(result["duplicate_count"], 2)

    def test_canonical_url_does_not_block_dedupe(self):
        text = make_text(800, 10)
        records = [record("a", text, canonical="https://origin.cn/x"),
                   record("b", text, canonical="https://other.cn/y")]
        self.assertEqual(dedupe.find_duplicates(records)["duplicate_count"], 1)

    def test_threshold_changes_result(self):
        base = unique_text(2000)
        mutated = base[:900] + unique_text(200, 0x8000) + base[1100:]
        records = [record("a", base), record("b", mutated)]
        strict = dedupe.find_duplicates(records, threshold=0.85)
        loose = dedupe.find_duplicates(records, threshold=0.7)
        self.assertEqual(strict["duplicate_count"], 0)
        self.assertEqual(loose["duplicate_count"], 1)

    def test_shared_nav_header_is_not_duplicate(self):
        header = "首页新闻政务公开政务服务互动交流走进贵州" * 5
        records = [record("a", header + unique_text(2000, 0x4E00)),
                   record("b", header + unique_text(2000, 0x8000))]
        self.assertEqual(dedupe.find_duplicates(records)["duplicate_count"], 0)

    def test_transitive_chain_forms_one_group(self):
        base = unique_text(3000)
        middle = base[:1000] + unique_text(200, 0x8000) + base[1200:]
        last = middle[:1800] + unique_text(200, 0x9000) + middle[2000:]
        records = [record("a", base, published="2026-09-01"),
                   record("b", middle, published="2026-09-02"),
                   record("c", last, published="2026-09-03")]
        # 第一段变异只影响 A~B 之外的一小段：A~B 达标、B~C 达标、A~C 不达标
        result = dedupe.find_duplicates(records, threshold=0.85)
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["keep"], "a")
        self.assertEqual(result["duplicate_count"], 2)

    def test_text_key_is_configurable(self):
        text = make_text(700, 11)
        records = [{"record_id": "a", "body": text},
                   {"record_id": "b", "body": text}]
        self.assertEqual(dedupe.find_duplicates(records, text_key="body")
                         ["duplicate_count"], 1)

    def test_empty_input(self):
        for value in ([], None, ()):
            self.assertEqual(dedupe.find_duplicates(value),
                             {"groups": [], "duplicate_count": 0, "skipped": 0})

    def test_deterministic_and_order_independent(self):
        _, records = self._trio()
        records.append(record("unrelated", make_text(1200, 12)))
        first = dedupe.find_duplicates(records)
        self.assertEqual(dedupe.find_duplicates(records), first)
        shuffled = list(records)
        random.Random(13).shuffle(shuffled)
        self.assertEqual(dedupe.find_duplicates(shuffled), first)

    def test_groups_and_drops_are_sorted(self):
        text_a = make_text(600, 14)
        text_b = make_text(600, 15)
        records = [
            record("z1", text_a, published="2026-09-01"),
            record("z2", text_a, published="2026-09-02"),
            record("a1", text_b, published="2026-09-01"),
            record("a2", text_b, published="2026-09-02"),
        ]
        result = dedupe.find_duplicates(records)
        self.assertEqual([group["keep"] for group in result["groups"]], ["a1", "z1"])
        for group in result["groups"]:
            drops = [drop["record_id"] for drop in group["drops"]]
            self.assertEqual(drops, sorted(drops))

    def test_non_duplicate_records_are_not_grouped(self):
        records = [record(f"r{index}", make_text(500, index)) for index in range(10)]
        result = dedupe.find_duplicates(records)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["duplicate_count"], 0)


class TestGroupByCanonical(unittest.TestCase):
    """canonical_url 归并线索。"""

    def test_same_canonical_is_grouped(self):
        records = [record("b", "x", canonical="https://origin.cn/a"),
                   record("a", "y", canonical="https://origin.cn/a")]
        result = dedupe.group_by_canonical(records)
        self.assertEqual(result["groups"],
                         [{"canonical_url": "https://origin.cn/a",
                           "record_ids": ["a", "b"]}])
        self.assertEqual(result["orphans"], [])

    def test_no_canonical_are_orphans(self):
        records = [record("a", "x"), record("b", "y")]
        result = dedupe.group_by_canonical(records)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["orphans"], ["a", "b"])

    def test_unique_canonical_is_orphan(self):
        records = [record("a", "x", canonical="https://a.cn/1"),
                   record("b", "y", canonical="https://b.cn/2")]
        result = dedupe.group_by_canonical(records)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["orphans"], ["a", "b"])

    def test_top_level_canonical_fallback(self):
        records = [{"record_id": "a", "canonical_url": "https://origin.cn/x"},
                   {"record_id": "b", "canonical_url": "https://origin.cn/x"}]
        self.assertEqual(len(dedupe.group_by_canonical(records)["groups"]), 1)

    def test_missing_record_id_is_ignored(self):
        records = [{"canonical_url": "https://origin.cn/x"},
                   record("b", "y", canonical="https://origin.cn/x")]
        result = dedupe.group_by_canonical(records)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["orphans"], ["b"])

    def test_empty_input(self):
        self.assertEqual(dedupe.group_by_canonical([]), {"groups": [], "orphans": []})
        self.assertEqual(dedupe.group_by_canonical(None), {"groups": [], "orphans": []})


class TestContentKey(unittest.TestCase):
    """内容键：sha256 优先，否则稳定文本指纹。"""

    def test_sha256_preferred(self):
        self.assertEqual(dedupe.content_key({"content_sha256": "abc", "text": "正文"}),
                         "sha256:abc")

    def test_text_fingerprint_fallback(self):
        key = dedupe.content_key({"text": "酸汤鱼，产业"})
        self.assertTrue(key.startswith("text:"))
        self.assertEqual(len(key), len("text:") + 40)       # sha1 hex
        self.assertEqual(key, dedupe.content_key({"text": "酸汤鱼，产业"}))

    def test_normalization_equivalent_texts_share_key(self):
        self.assertEqual(dedupe.content_key({"text": "酸汤鱼，产业"}),
                         dedupe.content_key({"text": "酸汤鱼 产业！"}))

    def test_different_texts_differ(self):
        self.assertNotEqual(dedupe.content_key({"text": "酸汤鱼"}),
                            dedupe.content_key({"text": "稻花鱼"}))

    def test_missing_everything(self):
        self.assertEqual(dedupe.content_key({}), "")
        self.assertEqual(dedupe.content_key(None), "")
        self.assertEqual(dedupe.content_key({"text": None}), "")


class TestPerformance(unittest.TestCase):
    """100 条 × 5000 字，find_duplicates 必须 2 秒内跑完（预筛生效）。"""

    def test_hundred_records_within_budget(self):
        records = [{"record_id": f"r{index:03d}", "text": make_text(5000, index),
                    "source": {}} for index in range(100)]
        started = time.perf_counter()
        result = dedupe.find_duplicates(records, threshold=0.85)
        elapsed = time.perf_counter() - started
        self.assertEqual(result["duplicate_count"], 0)
        self.assertLess(elapsed, 2.0,
                        f"100 条 × 5000 字耗时 {elapsed * 1000:.0f} ms，超过 2000 ms 上限")


if __name__ == "__main__":
    unittest.main(verbosity=2)
