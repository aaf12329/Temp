# -*- coding: utf-8 -*-
"""正文容器定位模块的纯逻辑测试 —— 零网络、零真实数据写入。

运行（仓库根目录）：
    .\\myvenv\\Scripts\\python.exe -m unittest tests.test_boilerplate -v

覆盖：
    ① 常量词表          NOISE_TAGS / NOISE_PATTERNS 覆盖需求点名的标签与词
    ② strip_noise      标签与 class/id/role 去噪、嵌套深度配对、未闭合脚本、词元边界
    ③ 度量              text_len / link_density 的空片段、脚本跳过、取值范围
    ④ score_blocks      候选打分、正向词加分、按分数降序
    ⑤ find_main         文章页 / 列表页 / 纯文本回退 / 正文过短回退 / 契约键
    ⑥ article_body      验收场景（导航 20 链接 + 正文 3 段）、与 find_main 一致、稳定性
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from webcrawl import boilerplate          # noqa: E402

NAV_LABELS = [f"栏目{index}" for index in range(20)]
NAV_LINKS = "".join(f'<a href="/c{index}">{label}</a>' for index, label in enumerate(NAV_LABELS))

ARTICLE_HTML = (
    "<!DOCTYPE html><html><head><title>测试文章</title></head><body>"
    '<header class="site-header"><a href="/">首页</a></header>'
    f'<div class="nav">{NAV_LINKS}</div>'
    '<div class="main-content"><div class="article">'
    "<h1>酸汤鱼产业调查</h1>"
    '<div class="article-content">'
    "<p>第一段：凯里酸汤鱼是地方特色餐饮，产业规模持续扩大。</p>"
    "<p>第二段：2025年餐饮收入达到 412.68 亿元，同比增长 7.9%。</p>"
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

ARTICLE_PARAGRAPHS = ("第一段", "第二段", "第三段")
NAV_NOISE_TEXTS = ("首页", "栏目0", "栏目19", "热门推荐", "版权所有", "分享", "假标签")


class TestConstants(unittest.TestCase):
    """需求点名的标签与词必须都在公开词表里。"""

    def test_noise_tags_cover_requested_tags(self):
        required = {"nav", "header", "footer", "aside", "form", "iframe",
                    "script", "style", "noscript", "template", "svg"}
        self.assertTrue(required <= boilerplate.NOISE_TAGS,
                        sorted(required - boilerplate.NOISE_TAGS))

    def test_noise_patterns_cover_requested_words(self):
        required = {"nav", "menu", "footer", "header", "sidebar", "aside", "comment",
                    "share", "breadcrumb", "related", "recommend", "advert", "ad",
                    "banner", "copyright", "pagination", "toolbar"}
        self.assertTrue(required <= boilerplate.NOISE_PATTERNS,
                        sorted(required - boilerplate.NOISE_PATTERNS))

    def test_void_and_candidate_tags_are_sane(self):
        self.assertIn("br", boilerplate.VOID_TAGS)
        self.assertIn("input", boilerplate.VOID_TAGS)
        self.assertTrue({"body", "main", "article", "div"} <= boilerplate.CANDIDATE_TAGS)


class TestStripNoise(unittest.TestCase):
    """去噪：标签与 class/id/role 命中即整块删除，但绝对不能误删正文。"""

    def test_removes_page_noise_blocks(self):
        cleaned = boilerplate.strip_noise(ARTICLE_HTML)
        for noise in NAV_NOISE_TEXTS:
            self.assertNotIn(noise, cleaned, noise)
        for paragraph in ARTICLE_PARAGRAPHS:
            self.assertIn(paragraph, cleaned, paragraph)

    def test_nested_noise_is_removed_by_depth_pairing(self):
        """nav 里套多层 div：整段带走，后面的正文不动（正则非贪婪会只删到第一个闭标签）。"""
        html = (
            '<div class="nav"><div><span>导航甲</span></div><div>导航乙</div></div>'
            '<div class="article"><p>正文还在</p></div>'
        )
        cleaned = boilerplate.strip_noise(html)
        self.assertNotIn("导航甲", cleaned)
        self.assertNotIn("导航乙", cleaned)
        self.assertIn("正文还在", cleaned)

    def test_token_boundary_keeps_lookalike_content_class(self):
        """词元级匹配：commentary 不是 comment，不能被误删。"""
        html = '<div class="commentary"><p>评论性正文内容</p></div>'
        cleaned = boilerplate.strip_noise(html)
        self.assertIn("评论性正文内容", cleaned)

    def test_removes_role_navigation(self):
        html = '<div role="navigation"><a href="/a">栏目甲</a></div><p>正文</p>'
        cleaned = boilerplate.strip_noise(html)
        self.assertNotIn("栏目甲", cleaned)
        self.assertIn("正文", cleaned)

    def test_unclosed_script_is_removed_to_end(self):
        cleaned = boilerplate.strip_noise("<p>正文</p><script>var leaked = 1;")
        self.assertIn("正文", cleaned)
        self.assertNotIn("leaked", cleaned)

    def test_void_noise_tag_is_removed(self):
        cleaned = boilerplate.strip_noise('<p>正文</p><input type="text" name="q">')
        self.assertIn("正文", cleaned)
        self.assertNotIn("<input", cleaned)

    def test_html_without_noise_is_returned_unchanged(self):
        html = "<div><p>干干净净的正文。</p></div>"
        self.assertEqual(boilerplate.strip_noise(html), html)
        self.assertEqual(boilerplate.strip_noise(""), "")

    def test_strip_noise_is_idempotent(self):
        once = boilerplate.strip_noise(ARTICLE_HTML)
        self.assertEqual(boilerplate.strip_noise(once), once)

    def test_implicitly_closed_noise_wrapper_does_not_swallow_article(self):
        """源码缺闭标签时，噪声块只按"看起来像不像链接列表"决定整段删，不能连正文一起删。"""
        paragraphs = "".join(
            f"<p>第{index}段：正文内容较长，用来模拟真实文章，不能被缺失的闭标签连坐删除。</p>"
            for index in range(1, 5)
        )
        html = (
            '<body><div id="hwh-nav-wrap"><div class="nav">'
            f'<a href="/x">栏目</a></div><div class="article-content">{paragraphs}</div>'
            "</body></html>"
        )
        cleaned = boilerplate.strip_noise(html)
        self.assertNotIn("hwh-nav-wrap", cleaned)
        self.assertIn("第4段", cleaned)
        self.assertIn("第4段", boilerplate.article_body(html))


class TestMetrics(unittest.TestCase):
    """可见文字量与链接密度：纯 stdlib，跳过标签、脚本与样式。"""

    def test_text_len_ignores_tags_and_scripts(self):
        self.assertEqual(boilerplate.text_len("<p>你好 <b>world</b></p>"), 7)
        self.assertEqual(boilerplate.text_len("<script>var a = 1;</script>正文"), 2)
        self.assertEqual(boilerplate.text_len("<style>.x{}</style>正文"), 2)
        self.assertEqual(boilerplate.text_len(""), 0)

    def test_link_density_range_and_values(self):
        self.assertEqual(boilerplate.link_density('<a href="/x">链接</a>'), 1.0)
        self.assertAlmostEqual(
            boilerplate.link_density("<p>正文正文</p><a>链接</a>"), 2 / 6, places=6)
        self.assertEqual(boilerplate.link_density(""), 0.0)
        self.assertEqual(boilerplate.link_density("<p>正文</p>"), 0.0)

    def test_text_len_counts_chinese_and_ignores_whitespace(self):
        self.assertEqual(boilerplate.text_len("<p>酸 汤 鱼</p>"), 3)


class TestScoreBlocks(unittest.TestCase):
    """候选打分：正文容器必须比整页 body 分高（正向词加分生效）。"""

    def _by_class(self, candidates, class_name):
        for candidate in candidates:
            if class_name in candidate["class"].split():
                return candidate
        self.fail(f"候选里没有 class={class_name}：{[c['class'] for c in candidates]}")

    def test_candidates_are_sorted_and_positive(self):
        candidates = boilerplate.score_blocks(ARTICLE_HTML)
        self.assertTrue(candidates)
        scores = [candidate["score"] for candidate in candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for candidate in candidates:
            self.assertGreater(candidate["text_len"], 0)
            self.assertGreaterEqual(candidate["score"], 0.0)

    def test_article_content_scores_above_body(self):
        candidates = boilerplate.score_blocks(ARTICLE_HTML)
        article = self._by_class(candidates, "article-content")
        body = [candidate for candidate in candidates if candidate["tag"] == "body"][0]
        self.assertGreater(article["score"], body["score"])

    def test_metric_fields_are_present(self):
        candidate = boilerplate.score_blocks(ARTICLE_HTML)[0]
        for key in ("tag", "class", "id", "score", "text_len", "link_density",
                    "paragraphs", "punct", "link_count", "start", "end",
                    "inner_start", "inner_end"):
            self.assertIn(key, candidate)


class TestFindMain(unittest.TestCase):
    """主容器定位：文章 / 列表 / 回退三条路径。"""

    def test_article_page(self):
        result = boilerplate.find_main(ARTICLE_HTML)
        self.assertEqual(result["kind"], "article")
        self.assertIn("article", result["container"]["class"])

    def test_listing_page_is_classified_as_listing(self):
        listing = (
            "<html><body><div class=\"news-list\">" + "".join(
                f'<p><a href="/a{index}">新闻标题{index}</a></p>' for index in range(25)
            ) + "</div></body></html>"
        )
        self.assertEqual(boilerplate.find_main(listing)["kind"], "listing")

    def test_article_container_with_many_links_is_still_article(self):
        """新闻正文常给股票/关键词加链接：正文容器写了 article 就不能因链接多判成列表页。"""
        links = "".join(f'<a href="/k{index}">关键词{index}</a>' for index in range(40))
        paragraphs = "".join(
            f"<p>第{index}段正文，{links}指标继续增长。</p>" for index in range(5)
        )
        html = (
            '<html><body><div class="article-content">' + paragraphs + "</div></body></html>"
        )
        result = boilerplate.find_main(html)
        self.assertEqual(result["kind"], "article")
        self.assertIn("article", result["container"]["class"])

    def test_kind_follows_selected_container_not_loudest_wrapper(self):
        """外层链接很多、里层是干净正文时，kind 要跟选中的容器走（不能按最高分容器判）。"""
        links = "".join(f'<a href="/x{index}">栏目导航{index}</a>' for index in range(80))
        paragraphs = "".join(
            f"<p>第{index}段：正文段落内容，讲产业规模、增速与口径，不能当作链接列表。</p>"
            for index in range(25)
        )
        html = (
            "<html><body><div class=\"page-m4 wp\">"
            f'<div class="link-cloud">{links}</div>'
            f'<div class="col-l"><div class="dc-ccm1">{paragraphs}</div></div>'
            "</div></body></html>"
        )
        result = boilerplate.find_main(html)
        self.assertEqual(result["kind"], "article", result["reason"])
        self.assertNotIn("link-cloud", result["container"]["class"])

    def test_plain_text_falls_back_to_original(self):
        plain = "这是一段没有任何块级标签的纯文本。"
        result = boilerplate.find_main(plain)
        self.assertEqual(result["kind"], "unknown")
        self.assertEqual(result["html"], plain)
        self.assertIsNone(result["container"])
        self.assertEqual(result["candidates"], [])

    def test_too_short_container_falls_back_to_original(self):
        short = "<body><div>短</div></body>"
        result = boilerplate.find_main(short)
        self.assertEqual(result["kind"], "unknown")
        self.assertEqual(result["html"], short)

    def test_contract_keys_and_types(self):
        result = boilerplate.find_main(ARTICLE_HTML)
        self.assertEqual(set(result), {"html", "kind", "score", "reason",
                                       "container", "candidates"})
        self.assertIsInstance(result["html"], str)
        self.assertIn(result["kind"], ("article", "listing", "unknown"))
        self.assertIsInstance(result["score"], float)
        self.assertIsInstance(result["reason"], str)
        self.assertTrue(result["reason"].strip())
        self.assertIsInstance(result["candidates"], list)
        self.assertLessEqual(len(result["candidates"]), boilerplate.CANDIDATE_LIMIT)

    def test_stability_on_same_html(self):
        first = boilerplate.find_main(ARTICLE_HTML)
        second = boilerplate.find_main(ARTICLE_HTML)
        self.assertEqual(first, second)
        self.assertEqual(first["html"], second["html"])


class TestArticleBody(unittest.TestCase):
    """验收场景：导航 20 条链接 + 正文 3 段。"""

    def test_acceptance_nav_removed_and_body_kept(self):
        body = boilerplate.article_body(ARTICLE_HTML)
        for paragraph in ARTICLE_PARAGRAPHS:
            self.assertIn(paragraph, body, paragraph)
        for noise in NAV_NOISE_TEXTS:
            self.assertNotIn(noise, body, noise)
        self.assertEqual(len(NAV_LABELS), 20)

    def test_body_is_find_main_html(self):
        self.assertEqual(boilerplate.article_body(ARTICLE_HTML),
                         boilerplate.find_main(ARTICLE_HTML)["html"])

    def test_empty_input(self):
        self.assertEqual(boilerplate.article_body(""), "")
        self.assertEqual(boilerplate.find_main("")["kind"], "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
