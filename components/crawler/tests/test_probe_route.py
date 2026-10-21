# -*- coding: utf-8 -*-
"""判定层与选档层的纯逻辑测试 —— 零网络、零真实数据写入。

运行（仓库根目录）：
    .\\myvenv\\Scripts\\python.exe -m unittest discover -s tests -v

覆盖：
    ① probe.classify   8 个 verdict 的判定优先级（契约 docs\\抓取策略表.md §2）
    ② probe.classify   登录墙的两条回归：长文内嵌登录弹窗不算墙、被送去登录页才算墙
    ③ route.choose     verdict → tool 的选档表与降级链（不成环）
    ④ route.choose     残缺 profile 一律兜底为 skip，不抛异常
    ⑤ probe            profile 落盘 / 读回的往返与缺失容错
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from webcrawl import probe, route          # noqa: E402

STATIC_MIN = 300          # 与 probe.STATIC_TEXT_MIN 同义，写死便于发现常数被改


def signals(**overrides):
    """契约 §3 的 6 个固定键，默认全空。"""
    base = {"spa_mount": None, "noscript_hint": False, "login_form": False,
            "cookie_script": False, "cf_header": False, "script_ratio": None}
    base.update(overrides)
    return base


def judge(**kwargs):
    """跑一次 classify 并返回 verdict。"""
    return probe.classify(**kwargs)[0]


class TestClassify(unittest.TestCase):
    """8 个 verdict 的判定优先级。"""

    def test_all_verdicts_are_exactly_eight(self):
        self.assertEqual(len(probe.SIGNAL_KEYS), 6)
        self.assertEqual(len(route.VERDICTS), 8)
        self.assertEqual(len(set(route.VERDICTS)), 8)

    def test_static(self):
        self.assertEqual(judge(status=200, content_type="html", text_chars=800,
                               signals=signals()), "static")

    def test_thin_static(self):
        self.assertEqual(judge(status=200, content_type="html", text_chars=20,
                               signals=signals()), "thin_static")

    def test_dynamic_needs_short_text_and_evidence(self):
        spa = signals(spa_mount='id="app"')
        self.assertEqual(judge(status=200, content_type="html", text_chars=0,
                               signals=spa), "dynamic")
        # 正文够长时不许改判（真踩过：长文章里带 script 被误判动态）
        self.assertEqual(judge(status=200, content_type="html", text_chars=800,
                               signals=spa), "static")

    def test_blocked_by_status(self):
        self.assertEqual(judge(status=403, content_type=None, text_chars=None,
                               signals=signals()), "blocked")

    def test_pdf(self):
        self.assertEqual(judge(status=200, content_type="pdf", text_chars=None,
                               signals=signals()), "pdf")

    def test_robots_disallow_wins(self):
        self.assertEqual(
            judge(status=200, content_type="html", text_chars=800,
                  signals={**signals(), "robots_allowed": False}),
            "robots_disallow")

    def test_error_on_missing_status(self):
        self.assertEqual(judge(status=None, content_type=None, text_chars=None,
                               signals=signals()), "error")


class TestLoginWall(unittest.TestCase):
    """登录墙的两条回归：这是唯一会"整站被跳过"的误判，必须钉死。"""

    def test_short_page_with_embedded_form_is_login_wall(self):
        self.assertEqual(
            judge(status=200, content_type="html", text_chars=10,
                  signals=signals(login_form=True)),
            "login_wall")

    def test_long_article_with_embedded_popup_is_not_login_wall(self):
        """媒体页普遍内嵌隐藏登录浮层；正文够长时必须继续判 static。"""
        self.assertEqual(
            judge(status=200, content_type="html", text_chars=STATIC_MIN + 300,
                  signals=signals(login_form=True)),
            "static")

    def test_redirected_to_login_page_is_always_login_wall(self):
        """被重定向到登录页时，正文再长也是墙。"""
        self.assertEqual(
            judge(status=200, content_type="html", text_chars=STATIC_MIN + 300,
                  signals={**signals(), "login_url": True}),
            "login_wall")


class TestChoose(unittest.TestCase):
    """verdict → tool 的选档表与降级链。"""

    def test_every_verdict_maps_to_a_known_tool(self):
        for verdict in route.VERDICTS:
            decision = route.choose({"verdict": verdict})
            self.assertIn(decision["tool"], route.TOOLS, verdict)
            self.assertIsInstance(decision["reason"], str)
            self.assertTrue(decision["reason"].strip(), verdict)

    def test_unavailable_tiers_declare_their_dependency(self):
        for verdict, package in (("dynamic", "patchright"),
                                 ("blocked", "curl_cffi")):
            decision = route.choose({"verdict": verdict})
            self.assertFalse(decision["available"], verdict)
            self.assertEqual(decision["requires"], package, verdict)

    def test_zero_dependency_tiers_are_available(self):
        for verdict in ("static", "thin_static", "pdf", "robots_disallow",
                        "login_wall", "error"):
            decision = route.choose({"verdict": verdict})
            self.assertTrue(decision["available"], verdict)
            self.assertIsNone(decision["requires"], verdict)

    def test_fallback_chain_terminates_without_cycle(self):
        """blocked → impersonate → browser → skip，且绝不成环。"""
        seen, tool = [], route.choose({"verdict": "blocked"})["tool"]
        while tool is not None:
            self.assertNotIn(tool, seen, f"降级链成环：{seen}")
            seen.append(tool)
            tool = route.TOOL_REQUIRES.get(tool) or None   # 有依赖即不可用，但仍要能走到 skip
            tool = route.FALLBACK_OF.get(seen[-1])
        self.assertEqual(seen[:3], ["impersonate", "browser", "skip"])

    def test_broken_profiles_fall_back_to_skip(self):
        for broken in ({}, None, {"verdict": None}, {"verdict": "not_a_verdict"},
                       {"verdict": 123}, "Static"):
            decision = route.choose(broken)
            self.assertEqual(decision["tool"], "skip", broken)
            self.assertTrue(decision["available"], broken)

    def test_group_by_tool_covers_every_input_url(self):
        profiles = [{"url": "https://a.cn/1", "verdict": "static"},
                    {"url": "https://a.cn/2", "verdict": "static"},
                    {"url": "https://a.cn/3", "verdict": "blocked"}]
        grouped = route.group_by_tool(profiles)
        self.assertEqual(sorted(grouped), ["impersonate", "static"])
        self.assertEqual(len(grouped["static"]), 2)
        self.assertEqual(len(sum(grouped.values(), [])), 3)


class TestProfilePersistence(unittest.TestCase):
    """画像落盘：往返一致，缺失文件返回空字典（工具层不因缓存坏而崩）。"""

    def test_roundtrip_and_missing_file(self):
        profiles = [{"url": "https://a.cn/1", "verdict": "static"},
                    {"url": "https://a.cn/2", "verdict": "pdf"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "site_profile.json")
            probe.save_profiles(profiles, path)
            loaded = probe.load_profiles(path)
            self.assertEqual(sorted(loaded), ["https://a.cn/1", "https://a.cn/2"])
            self.assertEqual(loaded["https://a.cn/1"]["verdict"], "static")

            missing = probe.load_profiles(str(Path(tmp) / "nope.json"))
            self.assertEqual(missing, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
