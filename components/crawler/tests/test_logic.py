# -*- coding: utf-8 -*-
r"""纯逻辑测试 —— 零网络、零真实数据写入（《工程架构规范》§08）。

运行（仓库根目录）：
    .\myvenv\Scripts\python.exe -m unittest discover -s tests -v

覆盖：
    ① common.paths      地址簿是绝对路径且指向真实存在的种子文件
    ② common.io_utils   文本 / JSON / URL 清单读写往返，只在临时目录里发生
    ③ Robots_Detect     robots.txt 地址推导，以及用**假响应**替换网络后的取内容流程
    ④ Robots_Detect     页面类型判定（静态 HTML / JS 动态渲染），网络全部用假响应替换
"""

import gzip
import http.client
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from Path_Store import Path_Store as ps              # noqa: E402
from webcrawl import html_utils, io_utils, net, url_norm, web_check   # noqa: E402
from webcrawl import web_check as robots            # noqa: E402
from webcrawl import page as crawler                # noqa: E402
from extract import Extract_Article as extractor        # noqa: E402
from extract import Extract_Facts as facts_extractor    # noqa: E402


class TestPaths(unittest.TestCase):
    """路径地址簿：纯常量、绝对路径、指向真实存在的种子文件。"""

    def test_project_root_is_repo_root(self):
        self.assertEqual(os.path.realpath(ps.PROJECT_ROOT),
                         os.path.realpath(str(REPO_ROOT)))
        self.assertTrue(os.path.isfile(os.path.join(ps.PROJECT_ROOT, "README.md")))

    def test_seed_files_exist(self):
        self.assertGreaterEqual(len(ps.SEED_FILES), 3)   # 基线 3 个，允许扩充种子
        for seed in ps.SEED_FILES:
            self.assertIsInstance(seed, str)        # 地址簿用 os.path，常量是字符串
            self.assertTrue(os.path.isfile(seed), seed)

    def test_all_paths_are_absolute(self):
        for value in (ps.SRC_DIR, ps.DATA_DIR, ps.SITES_DIR):
            self.assertIsInstance(value, str)
            self.assertTrue(os.path.isabs(value), value)

    def test_outputs_live_under_the_right_layer(self):
        """解析产物必须在 data\\parsed，原始数据必须在 data\\raw（曾因默认值漏改落错位置）。"""
        for value in (ps.ARTICLES_JSON, ps.ARTICLES_CSV,
                      ps.FACTS_JSON, ps.FACTS_CSV):
            self.assertTrue(value.startswith(ps.PARSED_DIR + os.sep), value)
        for value in (ps.RAW_HTML_DIR, ps.PAGES_INDEX_JSON):
            self.assertTrue(value.startswith(ps.RAW_DIR + os.sep), value)


class TestIoUtils(unittest.TestCase):
    """I/O 单一入口：往返一致，且只在临时目录里写（不碰真实数据）。"""

    def test_text_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sub" / "note.txt"
            io_utils.write_text(target, "酸汤鱼\n稻花鱼\n")
            self.assertEqual(io_utils.read_text(target), "酸汤鱼\n稻花鱼\n")

    def test_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "r.json"
            payload = {"a": 1, "中文": ["x", "y"]}
            io_utils.write_json(target, payload)
            self.assertEqual(io_utils.read_json(target), payload)

    def test_read_url_lines_skips_blank_and_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "urls.md"
            io_utils.write_text(target, "https://a.example/1\n\n# 注释\n  \nhttps://b.example/2\n")
            self.assertEqual(io_utils.read_url_lines(target),
                             ["https://a.example/1", "https://b.example/2"])

    def test_make_file_name_is_url_determined(self):
        """文件名只由 URL 决定：同一 URL 覆盖同一文件，不同 URL 不撞名。"""
        url = "https://cinic.org.cn/xy/a.html"
        name = io_utils.make_file_name(url)
        self.assertEqual(name, io_utils.make_file_name(url))          # 幂等
        self.assertNotEqual(name, io_utils.make_file_name(url + "?p=2"))
        self.assertTrue(name.startswith("cinic.org.cn_"))
        self.assertTrue(name.endswith(".html"))
        # 端口里的冒号不能进文件名（Windows 非法字符）
        self.assertNotIn(":", io_utils.make_file_name("http://e.com:8080/x"))
        self.assertTrue(io_utils.make_file_name("http://e.com:8080/x").startswith("e.com-8080_"))
        # 后缀可换：PDF 走同一个规则
        self.assertTrue(io_utils.make_file_name(url, ".pdf").endswith(".pdf"))

    def test_write_csv_has_bom_and_header(self):
        """CSV 必须带 BOM + 表头。

        回归用：合并两处手写 CSV 时漏了 BOM，Excel 打开中文全是乱码。
        """
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "f.csv"
            rows = [{"a": 1, "b": "中文"}, {"a": 2, "b": "第二行"}]
            io_utils.write_csv(target, rows, ("a", "b"))
            raw = io_utils.read_bytes(target)
            self.assertEqual(raw[:3], b"\xef\xbb\xbf")                # BOM
            lines = io_utils.read_text(target).lstrip("\ufeff").splitlines()
            self.assertEqual(lines[0], "a,b")                        # 表头
            self.assertEqual(len(lines), 3)                          # 表头 + 2 行
            self.assertEqual(lines[1], "1,中文")

    def test_load_seed_urls_reads_the_address_book(self):
        """种子清单来自地址簿登记的全部文件；基线 91 条，只许增不许减。"""
        urls = io_utils.load_seed_urls()
        self.assertGreaterEqual(len(urls), 91)                       # 基线：2026-10-19 的 91 条
        self.assertEqual(len(io_utils.load_seed_urls(3)), 3)
        self.assertEqual(len(io_utils.load_seed_urls(0)), len(urls))   # 0 = 全部
        self.assertEqual(len(set(urls)), len(urls))                   # 种子不得有重复 URL
        for url in urls:
            self.assertTrue(url.startswith(("http://", "https://")), url)


class TestUrlNorm(unittest.TestCase):
    """URL 归一化（本阶段不接入采集流程，但规则必须是定的）。"""

    def test_hash_anchor_is_dropped(self):
        self.assertEqual(url_norm.normalize_url("https://a.com/x.html#top"),
                         "https://a.com/x.html")
        self.assertEqual(url_norm.normalize_url("https://a.com/x.html#a/b?c"),
                         "https://a.com/x.html")

    def test_scheme_is_unified_to_https(self):
        self.assertEqual(url_norm.normalize_url("http://a.com/x.html"),
                         "https://a.com/x.html")
        self.assertEqual(url_norm.normalize_url("a.com/x.html"),        # 裸域名
                         "https://a.com/x.html")

    def test_hostname_is_lowercased(self):
        self.assertEqual(url_norm.normalize_url("https://A.COM/X.html"),
                         "https://a.com/X.html")      # 只小写主机名，路径大小写敏感

    def test_default_ports_are_dropped(self):
        self.assertEqual(url_norm.normalize_url("https://a.com:443/x.html"),
                         "https://a.com/x.html")
        self.assertEqual(url_norm.normalize_url("http://a.com:80/x.html"),
                         "https://a.com/x.html")
        # 非默认端口必须保留，否则会指向错误的服务
        self.assertEqual(url_norm.normalize_url("https://a.com:8080/x.html"),
                         "https://a.com:8080/x.html")

    def test_query_and_trailing_slash_are_kept(self):
        """query 与末尾 `/` **不洗**——它们可能真的是不同页面，洗了会误合并。"""
        self.assertEqual(url_norm.normalize_url("https://a.com/x.html?id=1"),
                         "https://a.com/x.html?id=1")
        self.assertEqual(url_norm.normalize_url("https://a.com/x.html?utm_source=wx"),
                         "https://a.com/x.html?utm_source=wx")
        self.assertEqual(url_norm.normalize_url("https://a.com/x.html/"),
                         "https://a.com/x.html/")

    def test_same_page_different_spellings_collapse(self):
        """同一页面的三种写法必须归一化成同一个键（否则会存三份快照）。"""
        variants = ["http://a.com/x.html",
                    "https://a.com/x.html",
                    "https://a.com/x.html#top",
                    "https://A.COM:443/x.html"]
        keys = {url_norm.normalize_url(v) for v in variants}
        self.assertEqual(len(keys), 1, keys)
        self.assertEqual(keys.pop(), "https://a.com/x.html")

    def test_empty_and_garbage(self):
        self.assertEqual(url_norm.normalize_url(""), "")
        # 无法解析的不许抛异常（宁可留重复，也不误合并）
        self.assertIsInstance(url_norm.normalize_url("::::"), str)

    def test_url_key_is_alias(self):
        self.assertEqual(url_norm.url_key("http://a.com/x.html#top"),
                         url_norm.normalize_url("http://a.com/x.html#top"))


class TestRobotsText(unittest.TestCase):
    """robots.txt 地址推导 + 取内容流程（网络用假响应替换，零花费）。"""

    def test_build_robots_url(self):
        cases = [
            ("https://example.com/a/b?c=1", "https://example.com/robots.txt"),
            ("example.com", "https://example.com/robots.txt"),
            ("http://tjj.qdn.gov.cn/x.html", "http://tjj.qdn.gov.cn/robots.txt"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(robots.build_robots_url(url), expected)

    def test_fetch_robots_puts_content_in_variable(self):
        fake_body = "User-agent: *\nDisallow: /search\n"
        original = net.fetch
        try:
            net.fetch = lambda *a, **k: {"url": "x", "final_url": "x", "status": 200,
                                         "found": True, "text": fake_body, "headers": {},
                                         "error": None, "transient": False}
            report = robots.fetch_robots("https://example.com/page")
        finally:
            net.fetch = original
        self.assertTrue(report["ok"])
        self.assertEqual(report["content"], fake_body)      # 全文真的进了变量
        self.assertEqual(report["status"], 200)

    def test_fetch_robots_missing_file(self):
        original = net.fetch
        try:
            net.fetch = lambda *a, **k: {"url": "x", "final_url": "x", "status": 404,
                                         "found": False, "text": "", "headers": {},
                                         "error": None, "transient": False}
            report = robots.fetch_robots("https://example.com/")
        finally:
            net.fetch = original
        self.assertFalse(report["ok"])
        self.assertFalse(report["found"])       # 404 = 站点没有 robots.txt，不是错误
        self.assertEqual(report["content"], "")
        self.assertIsNone(report["error"])


class TestRobotsJudgement(unittest.TestCase):
    """robots 解析与判定：禁止清单 + 最长匹配（纯逻辑，无网络）。"""

    SAMPLE = ("User-agent: *\nDisallow: /search\nDisallow: /admin\n"
              "Allow: /admin/public\nCrawl-delay: 2\n\n"
              "User-agent: BadBot\nDisallow: /\n")

    def test_summarize_disallow_list(self):
        rules = robots.summarize_rules(robots.parse_robots(self.SAMPLE))
        self.assertEqual(rules["disallow"], ["/search", "/admin"])
        self.assertEqual(rules["allow"], ["/admin/public"])
        self.assertEqual(rules["crawl_delay"], 2.0)

    def test_judge_paths(self):
        parsed = robots.parse_robots(self.SAMPLE)
        self.assertIs(robots.is_allowed("/search?q=1", parsed)["allowed"], False)
        self.assertIs(robots.is_allowed("/admin/public/x", parsed)["allowed"], True)
        self.assertIs(robots.is_allowed("/product/1", parsed)["allowed"], True)

    def test_user_agent_isolation(self):
        parsed = robots.parse_robots(self.SAMPLE)
        self.assertIs(robots.is_allowed("/", parsed, "BadBot/1.0")["allowed"], False)
        self.assertIs(robots.is_allowed("/", parsed, "GoodBot/1.0")["allowed"], True)

    def test_missing_robots_means_unrestricted(self):
        """站上没有 robots.txt（404）→ 按 RFC 9309 视为无限制，allowed=True。

        回归用：曾把 404 当成"抓取失败"，导致一批没有 robots.txt 的站被误放弃。
        """
        original = net.fetch
        try:
            net.fetch = lambda *a, **k: {"url": "x", "final_url": "x", "status": 404,
                                         "found": False, "text": "", "encoding": None,
                                         "headers": {}, "error": None, "transient": False}
            report = web_check.check_robots("https://e.example/a.html", path="/a.html")
        finally:
            net.fetch = original
        self.assertIs(report["found"], False)
        self.assertIs(report["allowed"], True)
        self.assertIn("404", report["rule"])

    def test_check_robots_contract_with_fake_network(self):
        allow_all = {"site": "x", "robots_url": "x", "status": 200, "ok": True,
                     "content": "User-agent: *\nDisallow: /secret\n", "error": None}
        # 必须 patch web_check 自己的名字：patch 兼容别名 robots.fetch_robots 不生效
        original = web_check.fetch_robots
        try:
            web_check.fetch_robots = lambda *a, **k: allow_all
            # 只验 robots 契约：关掉页面类型探测，避免真的去抓页面（零网络自测）
            report = web_check.check_robots("https://e.example/page", path="/page",
                                            probe_render=False)
        finally:
            web_check.fetch_robots = original
        self.assertTrue(report["found"])
        self.assertIs(report["allowed"], True)
        self.assertEqual(report["rules"]["disallow"], ["/secret"])   # 禁止清单外露


class TestRenderDetection(unittest.TestCase):
    """页面类型判定（静态 HTML / JS 动态渲染）：纯逻辑 + 假 net.fetch 替换，零请求。"""

    LONG_TEXT = "凯里酸汤鱼产业调查正文。" * 80          # 880 字，稳稳超过静态阈值
    STATIC_HTML = ('<html><head><meta name="generator" content="WordPress 6.4"></head>'
                   '<body><div class="article">' + LONG_TEXT + "</div></body></html>")
    SPA_HTML = ('<html><body><div id="app"></div><script>'
                + 'var d = {"k": "v"};' * 60 + "</script></body></html>")
    NEXT_HTML = ('<html><body><div id="__next"></div>'
                 '<script id="__NEXT_DATA__" type="application/json">'
                 '{"props":{"pageProps":{}}}</script></body></html>')
    ANTI_BOT_HTML = ('<html><body><script>document.cookie="acw_sc__v2=abc";'
                     'location.href=location.pathname+location.search;</script></body></html>')

    @staticmethod
    def _response(url, text="", status=200, found=True, error=None):
        """造一个和 net.fetch 返回结构一致的假响应。"""
        return {"url": url, "final_url": url, "status": status, "found": found,
                "text": text, "encoding": "utf-8", "headers": {}, "error": error,
                "transient": False}

    def test_classify_render_five_kinds(self):
        """五种 HTML 的判定结果（参数化）：静态 / SPA / SSR 内嵌 / 反爬 / 空页。"""
        cases = [
            ("长正文静态页", self.STATIC_HTML, "static", 0.9),
            ("空壳 SPA", self.SPA_HTML, "dynamic", 0.85),
            ("内嵌 SSR 状态", self.NEXT_HTML, "dynamic", 0.85),
            ("JS 跳转反爬页", self.ANTI_BOT_HTML, "dynamic", 0.9),
            ("空字符串", "", "unknown", 0.0),
        ]
        for name, html, mode, confidence in cases:
            with self.subTest(case=name):
                result = robots.classify_render(html)
                self.assertEqual(result["mode"], mode)
                self.assertAlmostEqual(result["confidence"], confidence)
                self.assertEqual(set(result),
                                 {"mode", "confidence", "reason", "text_len",
                                  "script_bytes", "script_ratio", "signals"})
                self.assertEqual(set(result["signals"]),
                                 {"spa_mount", "ssr_state", "noscript_hint",
                                  "js_redirect", "meta_generator"})
        static = robots.classify_render(self.STATIC_HTML)
        self.assertGreaterEqual(static["text_len"], robots.VISIBLE_TEXT_MIN_STATIC)
        self.assertEqual(static["signals"]["meta_generator"], "WordPress 6.4")   # 只作证据

    def test_ssr_state_hint_in_reason(self):
        """内嵌 __NEXT_DATA__ 且正文极少：判动态，并点明可绕过渲染直接取 JSON。"""
        result = robots.classify_render(self.NEXT_HTML)
        self.assertEqual(result["signals"]["ssr_state"], "__NEXT_DATA__")
        self.assertLess(result["text_len"], robots.VISIBLE_TEXT_WEAK_STATIC)
        self.assertIn("__NEXT_DATA__", result["reason"])
        self.assertIn("可绕过渲染直接取", result["reason"])

    def test_html_text_skips_script(self):
        """去标签走共用工具 html_utils.to_text（原 robots.html_text 已合并进去）。"""
        sample = "<html><body><script>var a=1;</script><p>酸汤鱼</p></body></html>"
        self.assertEqual(html_utils.to_text(sample), "酸汤鱼")
        self.assertEqual(html_utils.to_text(""), "")

    def test_long_article_with_js_tool_codes_is_static(self):
        """回归：正文够长的文章页里出现 document.cookie / location.href= 仍是静态直出。

        真踩过：新浪财经、腾讯新闻正文 2500~6400 字，因为页内带了统计和跳转工具函数，
        被 `js_redirect` 无脑短路判成 dynamic —— 正文已经在 HTML 里，判动态是错的信号。
        """
        html = ('<html><body><div class="article">' + self.LONG_TEXT + "</div>"
                '<script>document.cookie="sc_gid=1";window.location.href='
                'window.location.href;</script></body></html>')
        result = robots.classify_render(html)
        self.assertTrue(result["signals"]["js_redirect"])       # 特征确实命中了
        self.assertGreaterEqual(result["text_len"], robots.VISIBLE_TEXT_MIN_STATIC)
        self.assertEqual(result["mode"], "static")              # 但正文优先 → 静态
        self.assertAlmostEqual(result["confidence"], 0.9)

    def test_script_ratio_is_byte_over_byte(self):
        """脚本占比的分子分母单位必须一致，否则中文页会被虚假放大、偏判动态。"""
        body = "中文正文。" * 50
        html = f"<html><body>{body}<script>{'var a=1;' * 10}</script></body></html>"
        result = robots.classify_render(html)
        self.assertLessEqual(result["script_ratio"], 1.0)
        expected = (len(("var a=1;" * 10).encode("utf-8"))
                    / len(html.encode("utf-8")))
        self.assertAlmostEqual(result["script_ratio"], expected)

    def test_probe_render_off_returns_none(self):
        """probe_render=False：不探测页面类型，render 必须是 None。"""
        original = net.fetch
        try:
            net.fetch = lambda *a, **k: self._response(
                "x", "User-agent: *\nDisallow: /admin\n")
            report = web_check.check_robots("https://e.example/p", probe_render=False)
        finally:
            net.fetch = original
        self.assertIsNone(report["render"])

    def test_default_probe_classifies_static_page(self):
        """默认开启探测：robots 放行后抓页面 → 长正文静态页。"""
        calls = []
        original = net.fetch
        try:
            def fake(url, **kwargs):
                calls.append(url)
                if url.endswith("/robots.txt"):
                    return self._response(url, "User-agent: *\nDisallow: /admin\n")
                return self._response(url, self.STATIC_HTML)
            net.fetch = fake
            report = web_check.check_robots("https://e.example/a.html", path="/a.html")
        finally:
            net.fetch = original
        self.assertIs(report["allowed"], True)
        self.assertEqual(report["render"]["mode"], "static")
        self.assertEqual(report["render"]["page_url"], "https://e.example/a.html")
        self.assertEqual(calls, ["https://e.example/robots.txt",
                                 "https://e.example/a.html"])       # robots + 页面

    def test_robots_disallow_never_requests_page(self):
        """合规红线：robots 明确禁止时**绝不**去取页面（哪怕开着探测）。"""
        calls = []
        original = net.fetch
        try:
            def fake(url, **kwargs):
                calls.append(url)
                return self._response(url, "User-agent: *\nDisallow: /\n")
            net.fetch = fake
            report = web_check.check_robots("https://e.example/a.html", path="/a.html",
                                         probe_render=True)
        finally:
            net.fetch = original
        self.assertIs(report["allowed"], False)
        self.assertIsNone(report["render"])
        self.assertEqual(calls, ["https://e.example/robots.txt"])   # 页面请求根本没发

    def test_page_html_skips_second_fetch(self):
        """调用方已经抓过页面：直接用给的 HTML 判定，不再发第二次请求。"""
        calls = []
        original = net.fetch
        try:
            def fake(url, **kwargs):
                calls.append(url)
                return self._response(url, "User-agent: *\nDisallow: /admin\n")
            net.fetch = fake
            report = web_check.check_robots("https://e.example/a.html",
                                         page_html=self.STATIC_HTML)
        finally:
            net.fetch = original
        self.assertEqual(report["render"]["mode"], "static")
        self.assertEqual(len(calls), 1)          # 只有 robots.txt
        self.assertTrue(calls[0].endswith("/robots.txt"))

    def test_detect_render_mode_normalizes_scheme(self):
        """只给域名（不带 http://）也要能探测 —— 和 build_robots_url 的容忍度一致。"""
        seen = []
        original = net.fetch
        try:
            def fake(url, **kwargs):
                seen.append(url)
                return self._response(url, self.STATIC_HTML)
            net.fetch = fake
            result = robots.detect_render_mode("example.com/a.html")
        finally:
            net.fetch = original
        self.assertEqual(seen, ["https://example.com/a.html"])   # 补了 https://
        self.assertEqual(result["mode"], "static")

    def test_detect_render_mode_failure_is_not_raised(self):
        """取页面失败不抛异常：mode=unknown、error 有值、reason 写明原因。"""
        original = net.fetch
        try:
            net.fetch = lambda *a, **k: self._response(
                "x", "", status=None, error="TimeoutError: x")
            result = robots.detect_render_mode("https://e.example/a.html")
        finally:
            net.fetch = original
        self.assertEqual(result["mode"], "unknown")
        self.assertTrue(result["error"])
        self.assertIn("TimeoutError", result["reason"])


class TestNetHelpers(unittest.TestCase):
    """net 的解压与解码（纯函数，不联网）。"""

    def test_gzip_is_decompressed(self):
        raw = gzip.compress("<html><title>酸汤鱼</title></html>".encode("utf-8"))
        out = net._decompress(raw, {"content-encoding": "gzip"})
        self.assertIn("酸汤鱼", out.decode("utf-8"))
        self.assertTrue(out.startswith(b"<html>"))

    def test_plain_body_untouched(self):
        body = b"<html>plain</html>"
        self.assertEqual(net._decompress(body, {}), body)

    def test_gbk_is_decoded(self):
        text, encoding = net._decode_body("凯里酸汤鱼".encode("gbk"),
                                          {"content-type": "text/html; charset=gbk"})
        self.assertEqual(text, "凯里酸汤鱼")
        self.assertEqual(encoding, "gbk")

    def test_meta_charset_fallback(self):
        html = '<html><head><meta charset="gb2312"></head><body>酸汤鱼</body></html>'
        text, encoding = net._decode_body(html.encode("gb2312"), {})
        self.assertIn("酸汤鱼", text)
        self.assertIn(encoding, ("gb2312", "gbk", "gb18030"))


class TestNetRetry(unittest.TestCase):
    """net.fetch 的重试语义：只重试暂时性错误，传输中断不能穿透上层。

    ⚠️ 不打真实网络：把 `net.urlopen` 换成假实现，退避也不真睡。
    """

    class _Response:
        """假的 urlopen 返回对象，`read()` 的行为由调用方注入。"""

        def __init__(self, payload=b"", status=200, headers=None, reader=None):
            self.status = status
            self.headers = headers or {}
            self._payload = payload
            self._reader = reader

        def read(self):
            if self._reader is not None:
                return self._reader()
            return self._payload

        def geturl(self):
            return "https://e.example/x"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _run_fetch(self, opener, retries=2):
        """在假 urlopen 下跑一次 fetch，返回 (结果, 被调用次数)。"""
        original_open, original_backoff = net.urlopen, net._sleep_backoff
        calls = {"n": 0}

        def counting_open(*args, **kwargs):
            calls["n"] += 1
            return opener(calls["n"])

        net.urlopen = counting_open
        net._sleep_backoff = lambda attempt: None        # 不真睡，测试要快
        try:
            result = net.fetch("https://e.example/x.pdf", retries=retries)
        finally:
            net.urlopen, net._sleep_backoff = original_open, original_backoff
        return result, calls["n"]

    def test_incomplete_read_is_retried_as_transient(self):
        """回归：分块传输中途断开必须按暂时性错误重试。

        真踩过：抓 3.5 MB 的柳州日报 PDF 时崩在 `response.read()`；
        `IncompleteRead` 继承 `HTTPException` 而**不是** `OSError`，
        旧代码的 `except (URLError, TimeoutError, OSError)` 漏接 → 异常直接穿透上层。
        """
        state = {"n": 0}

        def reader():
            state["n"] += 1
            if state["n"] < 3:                          # 前两次断开
                raise http.client.IncompleteRead(b"x" * 100)
            return b"%PDF-1.4 ok"

        response = self._Response(reader=reader)
        result, calls = self._run_fetch(lambda attempt: response, retries=2)

        self.assertEqual(calls, 3)                      # 第 3 次才成功
        self.assertIsNone(result["error"])
        self.assertTrue(result["transient"])            # 标记经历过可重试错误
        self.assertEqual(result["raw"], b"%PDF-1.4 ok")

    def test_incomplete_read_exhausted_returns_error_not_raise(self):
        """重试用尽后必须**返回** error，而不是把异常抛给调用方。"""
        def reader():
            raise http.client.IncompleteRead(b"partial")

        result, calls = self._run_fetch(
            lambda attempt: self._Response(reader=reader), retries=1)

        self.assertEqual(calls, 2)                      # 首次 + 1 次重试
        self.assertIn("IncompleteRead", result["error"])
        self.assertTrue(result["transient"])
        self.assertEqual(result["raw"], b"")

    def test_http_exception_is_retried(self):
        """RemoteDisconnected / BadStatusLine 等协议级异常也算暂时性错误。"""
        def opener(attempt):
            if attempt < 2:
                raise http.client.RemoteDisconnected("closed without response")
            return self._Response(b"<html>ok</html>")
        result, calls = self._run_fetch(opener, retries=2)
        self.assertEqual(calls, 2)
        self.assertIsNone(result["error"])

    def test_client_error_is_not_retried(self):
        """4xx 是逻辑/权限错误，立刻暴露——重试只会掩盖问题。"""
        def opener(attempt):
            raise urllib.error.HTTPError("https://e.example/x", 403, "Forbidden", {}, None)
        result, calls = self._run_fetch(opener, retries=3)
        self.assertEqual(calls, 1)                      # 只发了一次
        self.assertEqual(result["status"], 403)
        self.assertIn("403", result["error"] or "")
        self.assertFalse(result["transient"])

    def test_transport_error_is_retried(self):
        """URLError（DNS 抖动 / 连接重置）属暂时性错误，应重试。"""
        def opener(attempt):
            if attempt < 2:
                raise urllib.error.URLError("dns hiccup")
            return self._Response("<html>酸汤鱼</html>".encode("utf-8"),
                                  headers={"content-type": "text/html; charset=utf-8"})
        result, calls = self._run_fetch(opener, retries=2)
        self.assertEqual(calls, 2)
        self.assertIsNone(result["error"])
        self.assertIn("酸汤鱼", result["text"])

    def test_bare_domain_gets_https(self):
        """裸域名（无 scheme）不许抛 ValueError，必须自动补 https。"""
        seen = {}
        original_open, original_backoff = net.urlopen, net._sleep_backoff
        try:
            def fake_open(request, *args, **kwargs):
                seen["url"] = request.full_url
                return self._Response(b"<html></html>")
            net.urlopen = fake_open
            net._sleep_backoff = lambda attempt: None
            result = net.fetch("a.com/x", retries=0)
        finally:
            net.urlopen, net._sleep_backoff = original_open, original_backoff
        self.assertEqual(seen["url"], "https://a.com/x")
        self.assertEqual(result["url"], "https://a.com/x")


class TestCrawl(unittest.TestCase):
    """爬虫：robots 闸门 → 抓正文 → 落盘。网络与 robots 全部用假实现，零花费。"""

    @staticmethod
    def _fake_gate(allowed, disallow=("/secret",), delay=None):
        return {"site": "x", "robots_url": "x", "path": "/", "status": 200,
                "found": True, "error": None, "content": "",
                "rules": {"disallow": list(disallow), "allow": [],
                          "crawl_delay": delay, "user_agent_groups": 1},
                "allowed": allowed, "rule": "Disallow: /secret"}

    @staticmethod
    def _fake_fetch(html, status=200):
        return {"url": "x", "final_url": "x", "status": status, "found": True,
                "text": html, "encoding": "utf-8", "headers":
                {"content-type": "text/html; charset=utf-8"},
                "error": None, "transient": False}

    def test_ok_path_saves_snapshot(self):
        original_gate, original_fetch = web_check.check_robots, net.fetch
        try:
            web_check.check_robots = lambda *a, **k: self._fake_gate(True)
            net.fetch = lambda *a, **k: self._fake_fetch(
                "<html><head><title>酸汤鱼</title></head><body>正文</body></html>")
            with tempfile.TemporaryDirectory() as tmp:
                record = crawler.crawl("https://e.example/a.html", out_dir=tmp)
                self.assertEqual(record["status"], "ok")
                self.assertEqual(record["title"], "酸汤鱼")
                self.assertEqual(len(record["sha256"]), 64)
                self.assertTrue(os.path.isfile(record["file"]))
                # 文件名只由 URL 决定：<站点>_<URL哈希10>.html
                self.assertTrue(os.path.basename(record["file"]).startswith("e.example_"))
                self.assertTrue(os.path.basename(record["file"]).endswith(".html"))
                self.assertEqual(os.path.dirname(record["file"]), tmp)

                # 同一 URL 再抓一次：必须覆盖同一个文件，不新增
                before = sorted(os.listdir(tmp))
                again = crawler.crawl("https://e.example/a.html", out_dir=tmp)
                self.assertEqual(sorted(os.listdir(tmp)), before)
                self.assertEqual(again["file"], record["file"])
        finally:
            web_check.check_robots, net.fetch = original_gate, original_fetch

    def test_robots_blocked_never_requests_page(self):
        original_gate, original_fetch = web_check.check_robots, net.fetch
        called = {"page": 0}
        try:
            web_check.check_robots = lambda *a, **k: self._fake_gate(False)
            def spy(*a, **k):
                called["page"] += 1
                return self._fake_fetch("x")
            net.fetch = spy
            record = crawler.crawl("https://e.example/a.html")
        finally:
            web_check.check_robots, net.fetch = original_gate, original_fetch
        self.assertEqual(record["status"], "robots_blocked")
        self.assertEqual(called["page"], 0)          # 关键：正文请求根本没发
        self.assertIsNone(record["file"])
        self.assertEqual(record["robots_disallow"], ["/secret"])

    def test_unknown_robots_is_treated_as_blocked(self):
        original_gate = web_check.check_robots
        try:
            web_check.check_robots = lambda *a, **k: self._fake_gate(None)
            record = crawler.crawl("https://e.example/a.html")
        finally:
            web_check.check_robots = original_gate
        self.assertEqual(record["status"], "robots_blocked")
        self.assertIsNone(record["robots_allowed"])

    def test_transient_robots_failure_retried_once(self):
        """robots 因网络抖动抓不到 → 补问一次；补问成功就该照常抓。"""
        calls = []
        original_gate, original_fetch = web_check.check_robots, net.fetch
        try:
            def flaky(*args, **kwargs):
                calls.append(kwargs.get("retries"))
                return self._fake_gate(True) if len(calls) > 1 else self._fake_gate(None)
            web_check.check_robots = flaky
            net.fetch = lambda *a, **k: self._fake_fetch("<html><title>t</title></html>")
            with tempfile.TemporaryDirectory() as tmp:
                record = crawler.crawl("https://e.example/a.html", out_dir=tmp, retries=1)
        finally:
            web_check.check_robots, net.fetch = original_gate, original_fetch
        self.assertEqual(len(calls), 2)          # 问了两次
        self.assertEqual(calls[1], 3)            # 第二次提高了重试次数
        self.assertEqual(record["status"], "ok")

    def test_fetch_failed(self):
        original_gate, original_fetch = web_check.check_robots, net.fetch
        try:
            web_check.check_robots = lambda *a, **k: self._fake_gate(True)
            broken = self._fake_fetch("", status=500)
            broken["error"] = "HTTP 500 Server Error"
            net.fetch = lambda *a, **k: broken
            with tempfile.TemporaryDirectory() as tmp:
                record = crawler.crawl("https://e.example/a.html", out_dir=tmp)
        finally:
            web_check.check_robots, net.fetch = original_gate, original_fetch
        self.assertEqual(record["status"], "fetch_failed")
        self.assertIn("500", record["error"])

    def test_no_save_keeps_file_none(self):
        original_gate, original_fetch = web_check.check_robots, net.fetch
        try:
            web_check.check_robots = lambda *a, **k: self._fake_gate(True)
            net.fetch = lambda *a, **k: self._fake_fetch("<html><title>t</title></html>")
            with tempfile.TemporaryDirectory() as tmp:
                record = crawler.crawl("https://e.example/a.html", out_dir=tmp, save=False)
                self.assertEqual(os.listdir(tmp), [])     # 不落盘时目录必须还是空的
        finally:
            web_check.check_robots, net.fetch = original_gate, original_fetch
        self.assertEqual(record["status"], "ok")
        self.assertIsNone(record["file"])

    def test_pdf_goes_to_files_dir_without_parsing(self):
        """PDF 走原始文件快照：不解析、render_mode 为 None、不进 HTML 目录。

        真踩过：种子里有 3 条是统计公报 PDF，走 HTML 流程会得到一堆乱码。
        """
        original_gate, original_fetch = web_check.check_robots, net.fetch
        try:
            web_check.check_robots = lambda *a, **k: self._fake_gate(True)
            net.fetch = lambda *a, **k: {
                "url": "x", "final_url": "x", "status": 200, "found": True,
                "text": "%PDF-1.4 乱码乱码", "raw": b"%PDF-1.4 binary",
                "encoding": "utf-8",
                "headers": {"content-type": "application/pdf"},
                "error": None, "transient": False}
            with tempfile.TemporaryDirectory() as tmp:
                record = crawler.crawl("https://a.gov.cn/tjgb.pdf", out_dir=tmp)
                self.assertEqual(sorted(os.listdir(tmp)),
                                 [os.path.basename(record["file"])])
        finally:
            web_check.check_robots, net.fetch = original_gate, original_fetch
        self.assertEqual(record["status"], "ok_pdf")
        self.assertEqual(record["kind"], "pdf")
        self.assertIsNone(record["render_mode"])          # PDF 不做页面类型判定
        self.assertTrue(record["file"].endswith(".pdf"))
        self.assertEqual(record["bytes"], len(b"%PDF-1.4 binary"))   # 存的是字节数


class TestExtractArticle(unittest.TestCase):
    """抽取与去重：正文提取、标题回退、指纹去重、CSV 落盘（全是临时目录）。"""

    HTML = ("<html><head><title> 凯里酸汤鱼 </title><script>var x=1;</script></head>"
            "<body><div>导航</div><p>第一段正文</p><p>第二段正文</p>"
            "<style>.a{}</style></body></html>")

    def test_text_extraction_skips_script_and_style(self):
        text = extractor.text_from_html(self.HTML)
        self.assertIn("第一段正文", text)
        self.assertNotIn("var x", text)
        self.assertNotIn(".a{}", text)
        self.assertNotIn("凯里酸汤鱼", text)      # head/title 不算正文

    def test_title_fallback(self):
        self.assertEqual(extractor.parse_title(self.HTML), "凯里酸汤鱼")
        self.assertIsNone(extractor.parse_title("<html></html>"))

    def test_fingerprint_ignores_whitespace(self):
        self.assertEqual(extractor.fingerprint("酸汤鱼"), extractor.fingerprint(" 酸 汤 鱼 "))

    def test_extract_one_from_snapshot_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = os.path.join(tmp, "a.html")
            with open(snapshot, "wb") as handle:
                handle.write(self.HTML.encode("utf-8"))
            record = extractor.extract_one(
                {"url": "https://e.example/a.html", "file": snapshot, "title": None,
                 "encoding": "utf-8", "fetched_at": "2026-10-19 10:00:00"}, raw_dir=tmp)
        self.assertEqual(record["site"], "e.example")
        self.assertEqual(record["title"], "凯里酸汤鱼")
        self.assertGreater(record["text_len"], 5)
        self.assertIsNone(record["error"])

    def test_missing_snapshot_reports_error(self):
        record = extractor.extract_one({"url": "https://e.example/x", "file": "不存在.html"})
        self.assertIn("快照不存在", record["error"])

    def test_duplicates_marked(self):
        same = extractor.fingerprint("同一篇内容")
        records = [{"url": "https://a/1", "text_len": 5, "fingerprint": same},
                   {"url": "https://b/2", "text_len": 5, "fingerprint": same},
                   {"url": "https://c/3", "text_len": 5, "fingerprint": "other"}]
        extractor.mark_duplicates(records)
        self.assertTrue(records[1]["is_duplicate"])
        self.assertEqual(records[1]["duplicate_of"], "https://a/1")
        self.assertFalse(records[2]["is_duplicate"])

    def test_save_outputs_writes_json_and_csv(self):
        records = [{"site": "e.example", "title": "酸汤鱼", "text_len": 10,
                    "is_duplicate": False, "duplicate_of": None,
                    "fetched_at": "2026-10-19 10:00:00", "encoding": "utf-8",
                    "url": "https://e.example/a", "snapshot": "x.html"}]
        with tempfile.TemporaryDirectory() as tmp:
            outputs = extractor.save_outputs(records, data_dir=tmp)
            self.assertTrue(os.path.isfile(outputs["json"]))
            self.assertTrue(os.path.isfile(outputs["csv"]))
            with open(outputs["csv"], encoding="utf-8-sig") as handle:
                rows = handle.read().splitlines()
        self.assertEqual(len(rows), 2)               # 表头 + 1 行
        self.assertIn("酸汤鱼", rows[1])


class TestExtractFacts(unittest.TestCase):
    """数字事实抽取：指标归类、数值单位、年份地域、导航过滤、去重（全离线）。"""

    CTX = {"url": "https://e.example/a", "site": "e.example", "title": "测试"}

    def test_metric_value_year_region(self):
        facts = facts_extractor.extract_from_text(
            "2025年黔东南州酸汤产业产值突破25亿元。", self.CTX)
        self.assertEqual(len(facts), 1)
        fact = facts[0]
        self.assertEqual(fact["metric"], "产值/营收")
        self.assertEqual((fact["value"], fact["unit"]), (25.0, "亿元"))
        self.assertEqual((fact["year"], fact["region"]), (2025, "黔东南"))
        self.assertIn("25亿元", fact["sentence"])

    def test_longest_unit_wins(self):
        facts = facts_extractor.extract_from_text(
            "三都县稻花鱼养殖面积达7.1万亩。", self.CTX)
        self.assertTrue(any(f["unit"] == "万亩" and f["value"] == 7.1 for f in facts), facts)

    def test_navigation_text_is_ignored(self):
        nav = "首页 新闻 产经要闻 部委动态 时政新闻 市场环境 统计数据 政策动态 9.54亿"
        self.assertEqual(facts_extractor.extract_from_text(nav, self.CTX), [])

    def test_short_fragment_ignored(self):
        self.assertEqual(facts_extractor.split_sentences("短。"), [])

    def test_dedupe_same_fact(self):
        same = [{"url": "u", "metric": "m", "value": 1.0, "unit": "亿元", "year": 2025},
                {"url": "u", "metric": "m", "value": 1.0, "unit": "亿元", "year": 2025},
                {"url": "u", "metric": "m", "value": 2.0, "unit": "亿元", "year": 2025}]
        self.assertEqual(len(facts_extractor.dedupe_facts(same)), 2)

    def test_save_outputs(self):
        facts = facts_extractor.extract_from_text("2026年贵州产值达120亿元。", self.CTX)
        with tempfile.TemporaryDirectory() as tmp:
            outputs = facts_extractor.save_outputs(facts, data_dir=tmp)
            self.assertTrue(os.path.isfile(outputs["json"]))
            self.assertTrue(os.path.isfile(outputs["csv"]))
            with open(outputs["csv"], encoding="utf-8-sig") as handle:
                rows = handle.read().splitlines()
        self.assertEqual(len(rows), 2)               # 表头 + 1 条
        self.assertIn("120", rows[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
