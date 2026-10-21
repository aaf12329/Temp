# -*- coding: utf-8 -*-
"""api 编排集成测试（打桩网络）—— 零网络、零真实 data/ 写入。

运行（仓库根目录）：
    .\\myvenv\\Scripts\\python.exe -m unittest tests.test_api_pipeline -v

覆盖（api.crawl 这一层的编排逻辑，不是各子模块的单测）：
    ① BFS 链接发现      种子页 → 3 详情 + 1 翻页 + 1 附件，候选带 discovered_from / depth
    ② max_urls 上限      max_urls=3 → 恰好 3 条，第 4 条候选不再发请求
    ③ 增量跳过           load_index 里的 fresh 记录 → fresh 列表，net.fetch 零调用
    ④ job 账本           JOBS_DIR 指向临时目录：文件落盘、终态、total / percent
    ⑤ resume 续跑        账本里 1 ok + 1 pending → 只抓 pending 那一条
    ⑥ 跨 URL 去重        两页正文相同 → 都保留，一条 meta.duplicate_of 指向另一条
    ⑦ proxies 透传       request["proxies"]={} → 打桩 net.fetch 的 kwargs 收到 {}
    ⑧ robots 闸门        robots.allowed=False → skipped + E_ROBOTS_DISALLOW，零请求
    ⑨ limit 层           crawl-delay=10 时 interval 就是 10.0（不截断）
    ⑩ validate 边界      max_depth=9 / max_workers=0 / max_urls=0 → E_BAD_REQUEST

隔离手法：
    - net.fetch / prober.probe / store 的写盘函数全部打桩，**绝不发真实请求**；
    - ps.JOBS_DIR / ps.SITE_POLICY_JSON 指向 tempfile，job 账本与站点策略不落真实 data/；
    - 默认 per_host_delay_s=0，限速器不产生真实等待，整批用例秒级完成。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from Path_Store import Path_Store as ps              # noqa: E402
from webcrawl import api, errors, job, net, record, store  # noqa: E402
from webcrawl import probe as prober                 # 与 api.py 同一别名

# ---------------------------------------------------------------- 测试数据
SEED_URL = "https://a.cn/list.html"
DETAIL_URLS = ("https://a.cn/n/1.html", "https://a.cn/n/2.html",
               "https://a.cn/n/3.html")
PAGE_URL = "https://a.cn/list_2.html"
PDF_URL = "https://a.cn/files/a.pdf"
CANDIDATE_URLS = set(DETAIL_URLS) | {PAGE_URL, PDF_URL}

# 列表页：详情链接必须落在**正文容器内**，否则 boilerplate 会把它们当导航删掉。
LIST_HTML = (
    "<html><head><title>产业新闻列表</title></head><body>"
    '<div class="main-content"><div class="article">'
    "<h1>产业新闻列表</h1>"
    '<div class="article-content">'
    "<p>第一段：凯里酸汤鱼产业规模持续扩大，餐饮收入同比增长明显，市场消费活力持续释放。</p>"
    "<p>第二段：贵州省统计局数据显示，2025年餐饮收入达到 412.68 亿元，同比增长 7.9%。</p>"
    "<p>第三段：上游木姜子种植面积约 3.2 万亩，加工企业 87 家，产业链条不断延伸。</p>"
    '<a href="/n/1.html">新闻一</a><a href="/n/2.html">新闻二</a><a href="/n/3.html">新闻三</a>'
    '<a href="/list_2.html">下一页</a><a href="/files/a.pdf">附件</a>'
    "</div></div></div></body></html>"
)


def detail_html(title: str = "详情页") -> str:
    """一篇文章页：正文够长、正文容器带正向词，供正常抓取 / 去重用。"""
    return (
        "<html><head><title>" + title + "</title></head><body>"
        '<div class="main-content"><div class="article-content">'
        "<h1>" + title + "</h1>"
        "<p>正文段落一：凯里酸汤鱼是贵州省黔东南州的传统特色餐饮，产业链覆盖养殖、加工与餐饮服务。</p>"
        "<p>正文段落二：2025年全州酸汤鱼餐饮收入达到 412.68 亿元，同比增长 7.9%，门店数量持续增加。</p>"
        "<p>正文段落三：上游木姜子种植面积约 3.2 万亩，加工企业 87 家，带动就业超过两万人。</p>"
        "</div></div></body></html>"
    )


def fake_response(url: str, html: str) -> dict:
    """构造与 net.fetch 同形状的响应（raw 与 text 保持一致，便于内容指纹）。"""
    return {"url": url, "final_url": url, "status": 200, "found": True,
            "text": html, "raw": html.encode("utf-8"), "encoding": "utf-8",
            "headers": {"content-type": "text/html; charset=utf-8"},
            "error": None, "transient": False}


def static_profile(url: str) -> dict:
    """可抓的静态页 profile（robots 明确允许）。"""
    return {"url": url, "site": record.site_of(url), "verdict": "static",
            "reason": "测试桩：静态可抓",
            "robots": {"found": True, "allowed": True,
                       "rule": "Allow: /", "crawl_delay": None}}


class ApiPipelineTestBase(unittest.TestCase):
    """公共夹具：tempfile 隔离 + 网络/落盘全部打桩。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.jobs_dir = os.path.join(self._tmp.name, "jobs")

        self.fetch_calls = []        # [(url, kwargs)]：打桩 net.fetch 的全部调用
        self.index_records = []      # store.load_index 的返回值（增量用）
        self.html_by_url = {}        # 指定 URL 的响应 HTML
        self.page_html = detail_html()   # 其余 URL 的默认响应
        self.probe_profile = None    # 需要特定 profile 时覆盖

        self._patchers = [
            mock.patch.object(ps, "JOBS_DIR", self.jobs_dir),
            mock.patch.object(ps, "SITE_POLICY_JSON",
                              os.path.join(self._tmp.name, "site_policy.json")),
            mock.patch.object(store, "ensure_dirs", lambda: None),
            mock.patch.object(store, "load_index", self._fake_load_index),
            mock.patch.object(store, "upsert_index", lambda records: len(records)),
            mock.patch.object(store, "save_raw", lambda *a, **k: None),
            mock.patch.object(store, "save_text", lambda *a, **k: None),
            mock.patch.object(store, "save_paragraphs", lambda *a, **k: None),
            mock.patch.object(store, "save_tables", lambda *a, **k: []),
            mock.patch.object(store, "save_pdf_text", lambda *a, **k: None),
            mock.patch.object(net, "fetch", self._fake_fetch),
            mock.patch.object(prober, "probe", self._fake_probe),
        ]
        for patcher in self._patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    # ---------------------------------------------------------- 打桩实现
    def _fake_load_index(self):
        """默认空索引；用例改 self.index_records 即可模拟"抓过 / 没抓过"。"""
        return list(self.index_records)

    def _fake_fetch(self, url, **kwargs):
        """打桩 net.fetch：记录调用与 kwargs，绝不发真实请求。"""
        self.fetch_calls.append((url, kwargs))
        if url in self.html_by_url:
            return fake_response(url, self.html_by_url[url])
        if url.lower().split("?")[0].endswith(".pdf"):
            return {"url": url, "final_url": url, "status": 200, "found": True,
                    "text": "", "raw": b"", "encoding": "utf-8",
                    "headers": {"content-type": "application/pdf"},
                    "error": None, "transient": False}
        return fake_response(url, self.page_html)

    def _fake_probe(self, url, **kwargs):
        """打桩 prober.probe：没传 profiles 的 URL 也走同一套静态画像。"""
        if self.probe_profile is not None:
            profile = dict(self.probe_profile)
            profile.setdefault("url", url)
            return profile
        return static_profile(url)

    # ---------------------------------------------------------- 辅助方法
    def make_request(self, urls, **overrides):
        """统一请求骨架：限速 0、少量并发，避免真实等待。"""
        request = {"job_id": "job-api-test", "urls": list(urls),
                   "per_host_delay_s": 0, "max_workers": 2}
        request.update(overrides)
        return request

    def fetched_urls(self):
        return [url for url, _kwargs in self.fetch_calls]


# ============================================================== ① BFS 链接发现
class TestCrawlDiscovery(ApiPipelineTestBase):
    """crawl 的 BFS：种子页 → 详情 / 翻页 / 附件进下一层，meta 还原来源。"""

    def test_bfs_collects_detail_pagination_and_attachment(self):
        """3 详情 + 1 翻页 + 1 附件全部进下一层：结果 1+5，候选带 discovered_from/depth。"""
        self.html_by_url[SEED_URL] = LIST_HTML
        result = api.crawl(self.make_request(
            [SEED_URL], discover=True, max_depth=1, max_urls=20))

        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(len(result["results"]), 6)

        seed, candidates = result["results"][0], result["results"][1:]
        self.assertEqual(seed["url"], SEED_URL)
        self.assertEqual(seed["meta"]["depth"], 0)
        self.assertNotIn("discovered_from", seed["meta"])
        self.assertEqual(seed["meta"]["links_found"],
                         {"detail": 3, "page": 1, "attach": 1})

        self.assertEqual({item["url"] for item in candidates}, CANDIDATE_URLS)
        for item in candidates:
            self.assertEqual(item["meta"]["discovered_from"], SEED_URL, item["url"])
            self.assertEqual(item["meta"]["depth"], 1, item["url"])
            self.assertIn(item["status"], ("ok", "ok_pdf"), item["url"])

        # 种子 1 次 + 5 个候选各 1 次
        self.assertEqual(len(self.fetch_calls), 6)
        self.assertEqual(set(self.fetched_urls()), {SEED_URL} | CANDIDATE_URLS)

    def test_max_urls_stops_before_the_fourth_candidate(self):
        """max_urls=3：结果恰好 3 条，第 4 条候选没有发请求。"""
        self.html_by_url[SEED_URL] = LIST_HTML
        result = api.crawl(self.make_request(
            [SEED_URL], discover=True, max_depth=1, max_urls=3))

        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(len(result["results"]), 3)
        self.assertEqual(len(self.fetch_calls), 3)
        self.assertEqual(set(self.fetched_urls()),
                         {SEED_URL, DETAIL_URLS[0], DETAIL_URLS[1]})
        self.assertNotIn(DETAIL_URLS[2], set(self.fetched_urls()))


# ============================================================== ② 增量跳过
class TestCrawlIncremental(ApiPipelineTestBase):
    """增量模式：load_index 里够新的成功记录直接跳过，不发请求。"""

    def test_fresh_record_skips_every_fetch(self):
        """fresh 记录（ok + 指纹 + 今天抓的）→ fresh 列表含该 URL，net.fetch 零调用。"""
        self.index_records = [{"url": SEED_URL, "status": "ok",
                               "content_sha256": "a" * 64,
                               "fetched_at": record.now_iso()}]
        result = api.crawl(self.make_request([SEED_URL], incremental=True))

        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(result["fresh"], [SEED_URL])
        self.assertEqual(result["results"], [])
        self.assertEqual(result["counts"].get("fresh"), 1)
        self.assertEqual(self.fetch_calls, [])

    def test_incremental_works_without_job_ledger(self):
        """回归：`save_job=False` 时增量仍生效（关账本不许把增量静默关掉、重抓）。"""
        self.index_records = [{"url": SEED_URL, "status": "ok",
                               "content_sha256": "a" * 64,
                               "fetched_at": record.now_iso()}]
        result = api.crawl(self.make_request([SEED_URL], incremental=True,
                                             save_job=False))

        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(result["fresh"], [SEED_URL])
        self.assertIsNone(result["job_status"])
        self.assertEqual(self.fetch_calls, [])


# ============================================================== ③ job 账本
class TestCrawlJobLedger(ApiPipelineTestBase):
    """crawl 的账本落盘：job 文件、终态、进度对得上结果。"""

    def test_job_file_and_progress_are_written(self):
        """JOBS_DIR 指向临时目录：crawl 后 job 文件存在、终态 done/partial、percent=100。"""
        result = api.crawl(self.make_request([DETAIL_URLS[0]], job_id="job-ledger"))

        path = job.job_path("job-ledger")
        self.assertTrue(os.path.isfile(path), f"job 账本没落盘：{path}")
        loaded = job.load_job("job-ledger")
        self.assertIsNotNone(loaded)
        self.assertIn(loaded["status"], ("done", "partial"))
        self.assertEqual(loaded["progress"]["total"], len(result["results"]))
        self.assertEqual(loaded["progress"]["percent"], 100.0)
        self.assertEqual(result["progress"]["total"], len(result["results"]))
        self.assertEqual(result["job_status"], loaded["status"])


# ============================================================== ④ resume 续跑
class TestCrawlResume(ApiPipelineTestBase):
    """resume_job：账本里已完成的 URL 跳过，只抓 pending 的那些。"""

    def test_resume_fetches_only_pending_url(self):
        """手工造 1 ok + 1 pending 的 job → resume 只抓 pending，net.fetch 恰好 1 次。"""
        done_url, pending_url = DETAIL_URLS[0], DETAIL_URLS[1]
        request = self.make_request([done_url, pending_url], job_id="job-resume")
        manual = job.new_job("job-resume", [done_url, pending_url],
                             request=request, started_at=record.now_iso())
        job.mark(manual, done_url, {"status": "ok", "record_id": "r-done",
                                    "error": None})
        job.save_job(manual)

        result = api.resume_job("job-resume")

        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(self.fetched_urls(), [pending_url])
        self.assertEqual([item["url"] for item in result["results"]], [pending_url])


# ============================================================== ⑤ 跨 URL 去重
class TestCrawlDedupe(ApiPipelineTestBase):
    """跨 URL 去重：只打戳不删记录（下游可自行决定怎么用）。"""

    def test_identical_pages_are_kept_but_stamped(self):
        """两页正文完全相同：两条都留在 results，其中一条 meta.duplicate_of 指向另一条。"""
        first, second = DETAIL_URLS[0], DETAIL_URLS[1]
        same_html = detail_html("同一篇通稿")
        self.html_by_url[first] = same_html
        self.html_by_url[second] = same_html

        result = api.crawl(self.make_request([first, second]))

        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual({item["url"] for item in result["results"]}, {first, second})
        self.assertTrue(all(item["status"] == "ok" for item in result["results"]))
        self.assertIsNotNone(result["dedupe"])
        self.assertGreaterEqual(result["dedupe"]["duplicate_count"], 1)

        by_record_id = {item["record_id"]: item for item in result["results"]}
        stamped = [item for item in result["results"]
                   if item["meta"].get("duplicate_of")]
        self.assertEqual(len(stamped), 1, "应恰好一条被盖重复戳")
        self.assertIn(stamped[0]["meta"]["duplicate_of"], by_record_id)
        self.assertNotEqual(stamped[0]["meta"]["duplicate_of"],
                            stamped[0]["record_id"])


# ============================================================== ⑥ proxies 透传
class TestCrawlProxies(ApiPipelineTestBase):
    """proxies 从 request 透传到 net.fetch 的 kwargs（{} = 显式不走代理）。"""

    def test_empty_proxies_reaches_fetch_kwargs(self):
        """request["proxies"]={} → 打桩 net.fetch 收到的 kwargs["proxies"] 就是 {}。"""
        result = api.crawl(self.make_request([DETAIL_URLS[0]], proxies={}))

        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(len(self.fetch_calls), 1)
        _url, kwargs = self.fetch_calls[0]
        self.assertIn("proxies", kwargs)
        self.assertEqual(kwargs["proxies"], {})


# ============================================================== ⑦ robots 闸门
class TestCrawlRobotsGate(ApiPipelineTestBase):
    """robots 闸门：allowed=False 时连页面请求都不发。"""

    def test_robots_disallow_skips_without_fetch(self):
        """profile.robots.allowed=False → 记录 skipped + E_ROBOTS_DISALLOW，零请求。"""
        blocked_profile = {"url": DETAIL_URLS[0], "site": "a.cn",
                           "verdict": "robots_disallow",
                           "robots": {"found": True, "allowed": False,
                                      "rule": "Disallow: /", "crawl_delay": None}}
        result = api.crawl(self.make_request([DETAIL_URLS[0]],
                                             profiles=[blocked_profile]))

        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(len(result["results"]), 1)
        item = result["results"][0]
        self.assertEqual(item["status"], "skipped")
        self.assertEqual(item["error"]["code"], errors.E_ROBOTS_DISALLOW)
        self.assertEqual(self.fetch_calls, [])


# ============================================================== ⑧ limit 层
class TestLimiterFor(unittest.TestCase):
    """_limiter_for：robots crawl-delay 绝不截断，人工默认值取最大。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 站点策略文件指向临时目录，避免真实 data/policy 影响间隔断言
        patcher = mock.patch.object(ps, "SITE_POLICY_JSON",
                                    os.path.join(self._tmp.name, "site_policy.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_crawl_delay_is_not_truncated(self):
        """crawl-delay=10 且 per_host_delay_s=1.0 → interval("a.cn") == 10.0。"""
        url = "https://a.cn/x.html"
        limiter = api._limiter_for(
            {"urls": [url], "per_host_delay_s": 1.0},
            {url: {"site": "a.cn", "robots": {"crawl_delay": 10}}})

        self.assertEqual(limiter.interval("a.cn"), 10.0)
        self.assertNotEqual(limiter.interval("a.cn"), 1.0 * 2)
        self.assertEqual(limiter.interval("未声明的站.cn"), 1.0)


# ============================================================== ⑨ validate 边界
class TestValidateBoundaries(unittest.TestCase):
    """validate 的并发 / 发现参数边界（越界一律 E_BAD_REQUEST）。"""

    def test_legal_request_passes(self):
        """边界内的请求返回 None。"""
        self.assertIsNone(api.validate({"urls": ["https://a.cn/1"], "job_id": "j"}))

    def test_out_of_range_arguments_are_bad_request(self):
        """max_depth=9 / max_workers=0 / max_urls=0 都返回 E_BAD_REQUEST。"""
        base = {"urls": ["https://a.cn/1"], "job_id": "j"}
        for field, value in (("max_depth", 9), ("max_workers", 0), ("max_urls", 0)):
            with self.subTest(field=field, value=value):
                error = api.validate(dict(base, **{field: value}))
                self.assertIsNotNone(error, f"{field}={value} 应被拒绝")
                self.assertEqual(error["code"], errors.E_BAD_REQUEST)


if __name__ == "__main__":
    unittest.main(verbosity=2)
