# -*- coding: utf-8 -*-
"""链接发现模块的纯逻辑测试 —— 零网络、零真实数据写入。

运行（仓库根目录）：
    .\\myvenv\\Scripts\\python.exe -m unittest tests.test_discover -v

覆盖：
    ① is_attachment / LINK_EXT        附件后缀识别（大小写、query、目录结尾）
    ② extract_links                   相对补全、去伪协议、去锚点、去重、rel/文字保留
    ③ same_site                       同 host 过滤（www 归一，保留元素类型）
    ④ list_detail_links               导航词 / 栏目根 / 附件 / 外站 / 短文字过滤
    ⑤ next_pages                      rel=next、文字提示、page=N（N>=2）
    ⑥ attachments                     ext 与文字
    ⑦ parse_sitemap                   urlset / sitemapindex、命名空间无关、&amp; 还原
    ⑧ parse_robots_sitemaps           大小写、注释、保序去重
    ⑨ merge_pool                      去重 / 限深 / 限数 / 稳定顺序 / dropped 计数
    ⑩ 确定性                          同一输入两次调用结果完全一致
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from webcrawl import discover          # noqa: E402

BASE = "https://www.example.com/news/index.html"

LIST_HTML = (
    "<html><head><title>产业新闻列表</title></head><body>"
    "<nav>"
    "<a href=\"/\">首页</a>"
    "<a href=\"/login\">登录</a>"
    "<a href=\"/about.html\">关于我们</a>"
    "<a href=\"javascript:void(0)\">展开</a>"
    "<a href=\"mailto:editor@example.com\">邮箱</a>"
    "<a href=\"tel:12345678\">电话</a>"
    "<a href=\"#top\">回到顶部</a>"
    "</nav>"
    "<ul class=\"list\">"
    "<a href=\"../article/1001.html\" rel=\"nofollow\">凯里酸汤鱼产业规模持续扩大</a>"
    "<a href=\"/article/1002.html\">酸汤鱼餐饮门店数量统计</a>"
    "<a href=\"/article/1001.html\">重复的旧链接</a>"
    "<a href=\"https://other.example.org/article/9.html\">外部转载</a>"
    "<a href=\"/article/short.html\">短</a>"
    "<a href=\"/news/\">栏目根</a>"
    "</ul>"
    "<div class=\"files\">"
    "<a href=\"/files/report.pdf\">2025年产业报告（PDF）</a>"
    "<a href=\"/files/data.xlsx?v=2\">数据附表</a>"
    "<a href=\"/files/archive.zip\">全部附件</a>"
    "</div>"
    "<div class=\"pager\">"
    "<a href=\"/news/index.html?page=1\">上一页</a>"
    "<a rel=\"next\" href=\"/news/index.html?page=2\">下一页</a>"
    "<a href=\"/news/index.html?page=8\">末页</a>"
    "<a rel=\"prev\" href=\"/news/index.html?page=1\">前一页</a>"
    "<a href=\"https://other.example.org/news?page=2\">下一页</a>"
    "</div>"
    "</body></html>")

URLSET = (
    "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
    "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
    "<url><loc> https://www.example.com/a.html?x=1&amp;y=2 </loc>"
    "<lastmod>2026-01-01</lastmod></url>"
    "<url><loc>https://www.example.com/b.html</loc></url>"
    "<url><loc>https://www.example.com/a.html?x=1&amp;y=2</loc></url>"
    "</urlset>")

SITEMAPINDEX = (
    "<sm:sitemapindex xmlns:sm=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
    "<sm:sitemap><sm:loc>https://www.example.com/sitemap-1.xml</sm:loc></sm:sitemap>"
    "<sm:sitemap><sm:loc>https://www.example.com/sitemap-2.xml</sm:loc></sm:sitemap>"
    "</sm:sitemapindex>")


class TestAttachment(unittest.TestCase):
    """① 附件后缀识别。"""

    def test_link_ext_contains_core_types(self):
        for ext in ("pdf", "doc", "docx", "xls", "xlsx", "csv", "zip"):
            self.assertIn(ext, discover.LINK_EXT)

    def test_true_for_attachment_suffix(self):
        self.assertTrue(discover.is_attachment("https://x.com/a.pdf"))
        self.assertTrue(discover.is_attachment("https://x.com/a.PDF"))
        self.assertTrue(discover.is_attachment("https://x.com/a.pdf?download=1"))
        self.assertTrue(discover.is_attachment("https://x.com/files/a.docx"))
        self.assertTrue(discover.is_attachment("https://x.com/files/a.tar.gz"))

    def test_false_for_non_attachment(self):
        self.assertFalse(discover.is_attachment(""))
        self.assertFalse(discover.is_attachment("https://x.com/"))
        self.assertFalse(discover.is_attachment("https://x.com/article/1001.html"))
        self.assertFalse(discover.is_attachment("https://x.com/download.php?id=1"))
        self.assertFalse(discover.is_attachment("https://x.com/a.pdf/"))


class TestExtractLinks(unittest.TestCase):
    """② <a href> 解析与补全。"""

    def test_relative_urls_are_resolved(self):
        links = discover.extract_links(LIST_HTML, BASE)
        urls = [link["url"] for link in links]
        self.assertIn("https://www.example.com/article/1001.html", urls)
        self.assertIn("https://www.example.com/article/1002.html", urls)
        self.assertIn("https://www.example.com/files/report.pdf", urls)

    def test_no_pseudo_scheme_leak(self):
        links = discover.extract_links(LIST_HTML, BASE)
        for link in links:
            self.assertNotIn("javascript:", link["url"])
            self.assertNotIn("mailto:", link["url"])
            self.assertNotIn("tel:", link["url"])
            self.assertNotIn("#", link["url"])

    def test_dedup_keeps_first(self):
        links = discover.extract_links(LIST_HTML, BASE)
        hits = [link for link in links
                if link["url"].endswith("/article/1001.html")]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["rel"], "nofollow")

    def test_http_and_https_are_same_page(self):
        html = ("<a href=\"http://example.com/a\">a</a>"
                "<a href=\"https://example.com/a\">b</a>")
        links = discover.extract_links(html, "https://example.com/")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["url"], "http://example.com/a")

    def test_text_is_collapsed(self):
        links = discover.extract_links(
            "<a href=\"/a.html\">  酸汤\n   鱼  </a>", BASE)
        self.assertEqual(links[0]["text"], "酸汤 鱼")

    def test_nested_tags_text_is_kept(self):
        links = discover.extract_links(
            "<a href=\"/a.html\"><span>酸汤</span>鱼</a>", BASE)
        self.assertEqual(links[0]["text"], "酸汤鱼")

    def test_rel_and_attachment_flag(self):
        links = discover.extract_links(LIST_HTML, BASE)
        pdf = next(link for link in links if link["url"].endswith("report.pdf"))
        self.assertTrue(pdf["is_attachment"])
        self.assertEqual(pdf["rel"], "")
        articles = [link for link in links if not link["is_attachment"]
                    and link["url"].endswith(".html")]
        self.assertTrue(articles)

    def test_external_link_is_kept_in_extract(self):
        urls = [link["url"] for link in discover.extract_links(LIST_HTML, BASE)]
        self.assertIn("https://other.example.org/article/9.html", urls)

    def test_protocol_relative_url(self):
        links = discover.extract_links(
            "<a href=\"//cdn.example.com/a.html\">a</a>", BASE)
        self.assertEqual(links[0]["url"], "https://cdn.example.com/a.html")

    def test_base_without_scheme(self):
        links = discover.extract_links(
            "<a href=\"a.html\">a</a>", "www.example.com/news/")
        self.assertEqual(links[0]["url"], "http://www.example.com/news/a.html")

    def test_unclosed_anchors_are_not_lost(self):
        links = discover.extract_links(
            "<a href=\"/a.html\">A<a href=\"/b.html\">B", BASE)
        self.assertEqual([link["url"] for link in links], [
            "https://www.example.com/a.html",
            "https://www.example.com/b.html",
        ])

    def test_empty_html(self):
        self.assertEqual(discover.extract_links("", BASE), [])


class TestSameSite(unittest.TestCase):
    """③ 同站过滤。"""

    def test_strips_www_on_both_sides(self):
        urls = ["https://example.com/a", "https://www.example.com/b"]
        self.assertEqual(discover.same_site(urls, "https://www.example.com/"),
                         urls)
        self.assertEqual(discover.same_site(urls, "https://example.com/"), urls)

    def test_filters_external(self):
        urls = ["https://www.example.com/a", "https://other.org/b"]
        self.assertEqual(discover.same_site(urls, "https://example.com/"),
                         ["https://www.example.com/a"])

    def test_keeps_dict_items_and_type(self):
        items = [{"url": "https://www.example.com/a", "x": 1},
                 {"url": "https://other.org/b", "x": 2}]
        kept = discover.same_site(items, "https://example.com/")
        self.assertEqual(kept, [{"url": "https://www.example.com/a", "x": 1}])

    def test_unknown_seed_returns_all(self):
        urls = ["https://a.com/1", "https://b.com/2"]
        self.assertEqual(discover.same_site(urls, ""), urls)

    def test_empty_input(self):
        self.assertEqual(discover.same_site([], "https://example.com/"), [])


class TestListDetailLinks(unittest.TestCase):
    """④ 列表页 → 详情候选。"""

    def test_only_article_pages_survive(self):
        details = discover.list_detail_links(LIST_HTML, BASE)
        self.assertEqual([item["url"] for item in details], [
            "https://www.example.com/article/1001.html",
            "https://www.example.com/article/1002.html",
        ])

    def test_nav_words_are_removed(self):
        details = discover.list_detail_links(LIST_HTML, BASE)
        texts = [item["text"] for item in details]
        for word in ("首页", "登录", "关于我们", "上一页", "下一页", "末页"):
            self.assertNotIn(word, texts)

    def test_section_root_is_removed(self):
        urls = [item["url"] for item in discover.list_detail_links(LIST_HTML, BASE)]
        self.assertNotIn("https://www.example.com/", urls)
        self.assertNotIn("https://www.example.com/news/", urls)

    def test_attachments_are_removed(self):
        urls = [item["url"] for item in discover.list_detail_links(LIST_HTML, BASE)]
        self.assertNotIn("https://www.example.com/files/report.pdf", urls)

    def test_external_is_removed(self):
        urls = [item["url"] for item in discover.list_detail_links(LIST_HTML, BASE)]
        self.assertNotIn("https://other.example.org/article/9.html", urls)

    def test_min_text(self):
        details = discover.list_detail_links(LIST_HTML, BASE)
        self.assertEqual(len(details), 2)
        more = discover.list_detail_links(LIST_HTML, BASE, min_text=1)
        self.assertEqual(len(more), 3)

    def test_score_prefers_detail_hint(self):
        details = discover.list_detail_links(LIST_HTML, BASE)
        self.assertTrue(all(item["score"] > 0 for item in details))

    def test_self_link_is_removed(self):
        html = "<a href=\"/list.html\">本页</a><a href=\"/article/1.html\">文章</a>"
        urls = [item["url"] for item in discover.list_detail_links(
            html, "https://example.com/list.html")]
        self.assertEqual(urls, ["https://example.com/article/1.html"])

    def test_self_link_with_query_is_removed(self):
        # 真实快照踩到：gov 专题页的「切换到电脑版」= 同路径 + ?isMobile=false
        html = ("<a href=\"/list.html?isMobile=false\">切换到电脑版</a>"
                "<a href=\"/article/1.html\">文章</a>")
        urls = [item["url"] for item in discover.list_detail_links(
            html, "https://example.com/list.html")]
        self.assertEqual(urls, ["https://example.com/article/1.html"])

    def test_more_with_decoration_is_nav(self):
        # 真实快照踩到：foodjx 栏目尾巴的「更多+」不是详情页
        html = ("<a href=\"/news/t0/list.html\">更多+</a>"
                "<a href=\"/article/1.html\">文章</a>")
        urls = [item["url"] for item in discover.list_detail_links(
            html, "https://example.com/news/")]
        self.assertEqual(urls, ["https://example.com/article/1.html"])

    def test_empty_html(self):
        self.assertEqual(discover.list_detail_links("", BASE), [])


class TestNextPages(unittest.TestCase):
    """⑤ 翻页链接。"""

    def test_finds_next_page_links(self):
        self.assertEqual(discover.next_pages(LIST_HTML, BASE), [
            "https://www.example.com/news/index.html?page=2",
            "https://www.example.com/news/index.html?page=8",
        ])

    def test_excludes_prev_and_page_one(self):
        pages = discover.next_pages(LIST_HTML, BASE)
        self.assertNotIn("https://www.example.com/news/index.html?page=1", pages)

    def test_excludes_external_next(self):
        pages = discover.next_pages(LIST_HTML, BASE)
        self.assertFalse(any("other.example.org" in page for page in pages))

    def test_dedup_same_next_url(self):
        html = ("<a rel=\"next\" href=\"/n?page=2\">下一页</a>"
                "<a href=\"/n?page=2\">下一页</a>")
        self.assertEqual(discover.next_pages(html, BASE),
                         ["https://www.example.com/n?page=2"])

    def test_rel_token_and_english_text(self):
        html = "<a rel=\"nofollow next\" href=\"/n?p=2\">Next</a>"
        self.assertEqual(discover.next_pages(html, BASE),
                         ["https://www.example.com/n?p=2"])

    def test_page_one_alone_is_not_next(self):
        html = "<a href=\"/n?page=1\">第一页</a>"
        self.assertEqual(discover.next_pages(html, BASE), [])

    def test_attachment_is_not_pagination(self):
        html = "<a href=\"/files/a.pdf?page=2\">下一页</a>"
        self.assertEqual(discover.next_pages(html, BASE), [])

    def test_empty_html(self):
        self.assertEqual(discover.next_pages("", BASE), [])


class TestAttachments(unittest.TestCase):
    """⑥ 附件清单。"""

    def test_ext_and_text(self):
        files = discover.attachments(LIST_HTML, BASE)
        self.assertEqual([item["ext"] for item in files], ["pdf", "xlsx", "zip"])
        self.assertEqual(files[0]["text"], "2025年产业报告（PDF）")
        self.assertEqual(files[1]["url"],
                         "https://www.example.com/files/data.xlsx?v=2")

    def test_no_attachment(self):
        self.assertEqual(discover.attachments("<a href=\"/a.html\">a</a>", BASE), [])

    def test_empty_html(self):
        self.assertEqual(discover.attachments("", BASE), [])


class TestSitemap(unittest.TestCase):
    """⑦ sitemap XML。"""

    def test_urlset_with_namespace_and_entities(self):
        parsed = discover.parse_sitemap(URLSET)
        self.assertEqual(parsed["urls"], [
            "https://www.example.com/a.html?x=1&y=2",
            "https://www.example.com/b.html",
        ])
        self.assertEqual(parsed["sitemaps"], [])

    def test_sitemapindex_with_prefix(self):
        parsed = discover.parse_sitemap(SITEMAPINDEX)
        self.assertEqual(parsed["sitemaps"], [
            "https://www.example.com/sitemap-1.xml",
            "https://www.example.com/sitemap-2.xml",
        ])
        self.assertEqual(parsed["urls"], [])

    def test_plain_no_namespace(self):
        xml = ("<urlset><url><loc>https://example.com/a</loc></url></urlset>")
        self.assertEqual(discover.parse_sitemap(xml)["urls"],
                         ["https://example.com/a"])

    def test_dedupe_and_whitespace(self):
        parsed = discover.parse_sitemap(URLSET)
        self.assertEqual(len(parsed["urls"]), 2)
        self.assertEqual(parsed["urls"][0], "https://www.example.com/a.html?x=1&y=2")

    def test_empty_and_invalid(self):
        self.assertEqual(discover.parse_sitemap(""),
                         {"urls": [], "sitemaps": []})
        self.assertEqual(discover.parse_sitemap("not xml"),
                         {"urls": [], "sitemaps": []})

    def test_self_closing_loc(self):
        xml = "<urlset><url><loc/></url></urlset>"
        self.assertEqual(discover.parse_sitemap(xml)["urls"], [])

    def test_extension_namespace_loc_is_ignored(self):
        # image sitemap：<image:loc> 是图片地址，不是页面 URL
        xml = ("<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\" "
               "xmlns:image=\"http://www.google.com/schemas/sitemap-image/1.1\">"
               "<url><loc>https://example.com/a.html</loc>"
               "<image:image><image:loc>https://example.com/a.jpg</image:loc>"
               "</image:image></url></urlset>")
        self.assertEqual(discover.parse_sitemap(xml)["urls"],
                         ["https://example.com/a.html"])


class TestRobotsSitemaps(unittest.TestCase):
    """⑧ robots.txt 的 Sitemap 行。"""

    def test_case_and_leading_spaces(self):
        text = ("User-agent: *\n"
                "Sitemap: https://example.com/sitemap.xml\n"
                "  sitemap: https://example.com/news.xml\n")
        self.assertEqual(discover.parse_robots_sitemaps(text), [
            "https://example.com/sitemap.xml",
            "https://example.com/news.xml",
        ])

    def test_comments_and_irrelevant_lines(self):
        text = ("# 主站地图\n"
                "Disallow: /sitemap.xml\n"
                "Sitemap: https://example.com/a.xml # 主图\n")
        self.assertEqual(discover.parse_robots_sitemaps(text),
                         ["https://example.com/a.xml"])

    def test_dedupe_and_empty(self):
        text = ("Sitemap: https://example.com/a.xml\n"
                "Sitemap: https://example.com/a.xml\n")
        self.assertEqual(discover.parse_robots_sitemaps(text),
                         ["https://example.com/a.xml"])
        self.assertEqual(discover.parse_robots_sitemaps(""), [])

    def test_utf8_bom(self):
        self.assertEqual(
            discover.parse_robots_sitemaps("\ufeffSitemap: https://example.com/a.xml\n"),
            ["https://example.com/a.xml"])


class TestMergePool(unittest.TestCase):
    """⑨ BFS 入池。"""

    def test_dict_items_keep_source_and_depth(self):
        result = discover.merge_pool(
            [{"url": "https://example.com/", "source": "seed", "depth": 0}],
            [{"url": "https://example.com/a", "source": "list", "depth": 1}],
            limit=10)
        self.assertEqual(result["pool"], [
            {"url": "https://example.com/", "source": "seed", "depth": 0},
            {"url": "https://example.com/a", "source": "list", "depth": 1},
        ])
        self.assertEqual(result["dropped"], 0)

    def test_strings_go_to_next_depth(self):
        result = discover.merge_pool(
            [{"url": "https://example.com/", "source": "seed", "depth": 0}],
            ["https://example.com/a", "https://example.com/b"], limit=10)
        self.assertEqual([item["depth"] for item in result["pool"]], [0, 1, 1])

    def test_empty_pool_strings_start_at_zero(self):
        result = discover.merge_pool([], ["https://example.com/a"], limit=10)
        self.assertEqual(result["pool"][0]["depth"], 0)

    def test_dedupe_counts_dropped(self):
        result = discover.merge_pool([], [
            "https://example.com/a",
            "https://example.com/a#top",
            "http://example.com/a",
        ], limit=10)
        self.assertEqual(len(result["pool"]), 1)
        self.assertEqual(result["dropped"], 2)

    def test_limit_truncates(self):
        result = discover.merge_pool(
            [{"url": "https://example.com/", "depth": 0}],
            ["https://example.com/a", "https://example.com/b",
             "https://example.com/c"], limit=2)
        self.assertEqual(len(result["pool"]), 2)
        self.assertEqual(result["dropped"], 2)

    def test_depth_cap(self):
        result = discover.merge_pool([], [
            {"url": "https://example.com/deep", "depth": discover.MAX_DEPTH + 1},
            {"url": "https://example.com/ok", "depth": discover.MAX_DEPTH},
        ], limit=10)
        self.assertEqual([item["url"] for item in result["pool"]],
                         ["https://example.com/ok"])
        self.assertEqual(result["dropped"], 1)

    def test_limit_zero(self):
        result = discover.merge_pool(
            [{"url": "https://example.com/", "depth": 0}],
            ["https://example.com/a"], limit=0)
        self.assertEqual(result["pool"], [])
        self.assertEqual(result["dropped"], 2)

    def test_invalid_items_are_dropped(self):
        result = discover.merge_pool([None, "", {}], ["https://example.com/a"],
                                     limit=10)
        self.assertEqual(len(result["pool"]), 1)
        self.assertEqual(result["dropped"], 3)

    def test_existing_duplicates(self):
        result = discover.merge_pool(
            ["https://example.com/a", "https://example.com/a"], [], limit=10)
        self.assertEqual(len(result["pool"]), 1)
        self.assertEqual(result["dropped"], 1)

    def test_empty_inputs(self):
        self.assertEqual(discover.merge_pool([], [], 10),
                         {"pool": [], "dropped": 0})


class TestDeterminism(unittest.TestCase):
    """⑩ 同一输入两次调用必须完全一致。"""

    def test_all_discovery_functions_are_stable(self):
        first = {
            "links": discover.extract_links(LIST_HTML, BASE),
            "details": discover.list_detail_links(LIST_HTML, BASE),
            "next": discover.next_pages(LIST_HTML, BASE),
            "files": discover.attachments(LIST_HTML, BASE),
            "sitemap": discover.parse_sitemap(URLSET),
            "robots": discover.parse_robots_sitemaps(
                "Sitemap: https://example.com/a.xml\n"),
            "pool": discover.merge_pool(
                [{"url": "https://example.com/", "depth": 0}],
                ["https://example.com/a", "https://example.com/b"], 10),
        }
        second = {
            "links": discover.extract_links(LIST_HTML, BASE),
            "details": discover.list_detail_links(LIST_HTML, BASE),
            "next": discover.next_pages(LIST_HTML, BASE),
            "files": discover.attachments(LIST_HTML, BASE),
            "sitemap": discover.parse_sitemap(URLSET),
            "robots": discover.parse_robots_sitemaps(
                "Sitemap: https://example.com/a.xml\n"),
            "pool": discover.merge_pool(
                [{"url": "https://example.com/", "depth": 0}],
                ["https://example.com/a", "https://example.com/b"], 10),
        }
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
