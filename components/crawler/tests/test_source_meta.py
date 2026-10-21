# -*- coding: utf-8 -*-
"""来源陈述模块测试 —— 零网络、零真实数据（《工程架构规范》§08）。

运行（仓库根目录）：
    .\\myvenv\\Scripts\\python.exe -m unittest tests.test_source_meta -v

覆盖：
    ① clean_declaration   垃圾声明（JS 模板残留 / HTML 实体 / 纯符号）判 None，空白归一
    ② org_core / same_org 州/市/县/人民政府/门户网 等通用后缀归一，同机构不同全称
    ③ extract().is_reprint 跨主体转载 True、同主体 False、判不了 None、转载类标签
    ④ 无声明等回 canonical 逻辑；publisher 清洗；接口字段名/签名保持不变
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from webcrawl import source_meta                      # noqa: E402

SOURCE_KEYS = ("publisher", "published_at", "canonical_url",
               "author", "declaration", "is_reprint")


def make_html(title=None, publisher=None, declaration=None,
              canonical=None) -> str:
    """拼一个最小页面：标题 / og:site_name / 「来源」声明 / canonical。"""
    head = ""
    if title:
        head += f"<title>{title}</title>"
    if publisher:
        head += f'<meta property="og:site_name" content="{publisher}">'
    if canonical:
        head += f'<link rel="canonical" href="{canonical}">'
    body = f"<p>{declaration}</p>" if declaration else ""
    return f"<html><head>{head}</head><body>{body}</body></html>"


class TestCleanDeclaration(unittest.TestCase):
    """声明清洗：垃圾丢弃、空白归一、像不像机构名。"""

    def test_js_template_residue_is_dropped(self):
        for raw in ("来源：'+SourceName)", '来源："+str)', "来源：'+wzly)"):
            self.assertIsNone(source_meta.clean_declaration(raw), raw)

    def test_html_entity_and_pure_symbol_is_dropped(self):
        for raw in ("来源：&nbsp", "来源：&nbsp;", "来源：   ", "来源：（）"):
            self.assertIsNone(source_meta.clean_declaration(raw), raw)

    def test_html_fragment_is_not_an_org_name(self):
        # 真实快照里出现过「来源：    主要包括」——正文残句不是机构名
        self.assertIsNone(source_meta.clean_declaration("来源：    主要包括"))

    def test_unbalanced_bracket_is_dropped(self):
        # 真实快照：来源：西江旅游公司）——右括号来自页面模板残留
        self.assertIsNone(source_meta.clean_declaration("来源：西江旅游公司）"))

    def test_whitespace_and_newline_are_normalized(self):
        raw = "来源： \n\t\t\t\t\t\t\t\t 贵州日报天眼新闻"
        self.assertEqual(source_meta.clean_declaration(raw), "贵州日报天眼新闻")
        self.assertEqual(source_meta.clean_declaration("来源：\n\t柳州日报&nbsp"),
                         "柳州日报")

    def test_label_and_space_are_stripped(self):
        self.assertEqual(source_meta.clean_declaration("来源:  新莓daybreak"),
                         "新莓daybreak")
        self.assertEqual(source_meta.clean_declaration("转载自黔东南新闻网"),
                         "黔东南新闻网")


class TestOrgCoreAndSameOrg(unittest.TestCase):
    """机构名归一：剥通用后缀，判是不是同一个主体。"""

    def test_generic_suffix_is_stripped(self):
        self.assertEqual(source_meta.org_core("黔东南州人民政府"), "黔东南")
        self.assertEqual(
            source_meta.org_core("黔东南苗族侗族自治州人民政府门户网"),
            "黔东南苗族侗族")
        self.assertEqual(source_meta.org_core("央广网"), "央广")
        self.assertEqual(source_meta.org_core("杭州网"), "杭州")

    def test_china_prefix_is_not_stripped_off_a_brand(self):
        # 「中国食品报」不能归一成「食品」，否则会和任意「食品*网」误判同主体
        self.assertEqual(source_meta.org_core("中国食品报"), "中国食品")

    def test_same_org_true_cases(self):
        self.assertTrue(source_meta.same_org(
            "黔东南州人民政府", "黔东南苗族侗族自治州人民政府门户网"))
        self.assertTrue(source_meta.same_org("央广网", "央广网"))
        self.assertTrue(source_meta.same_org("杭州网", "杭州网"))
        self.assertTrue(source_meta.same_org("麻江县农业农村局", "麻江县人民政府网"))

    def test_same_org_false_cases(self):
        self.assertFalse(source_meta.same_org("中国食品报", "食品机械设备网"))
        # 「中央广电总台国际在线」是母品牌全称，站点自称只有「国际在线」→ 不同主体
        self.assertFalse(source_meta.same_org(
            "中央广电总台国际在线", "国际在线"))
        # 同样以「黔东南」开头，但一个是新闻网、一个是农科院 → 不同主体
        self.assertFalse(source_meta.same_org(
            "黔东南新闻网", "黔东南苗族侗族自治州农业科学院"))
        self.assertFalse(source_meta.same_org("贵阳新闻", "贵阳市商务局"))


class TestIsReprint(unittest.TestCase):
    """is_reprint 判定：True / False / None 三态，宁可 None 不许猜 True。"""

    def test_interface_fields_are_unchanged(self):
        result = source_meta.extract(make_html(), "a.cn", "https://a.cn/")
        self.assertEqual(tuple(result.keys()), SOURCE_KEYS)

    def test_garbage_declaration_is_none_not_true(self):
        html = make_html(publisher="贵州省人民政府",
                         declaration="来源：'+SourceName)")
        self.assertIsNone(
            source_meta.extract(html, "www.guizhou.gov.cn",
                                "https://www.guizhou.gov.cn/a.html")["is_reprint"])

    def test_same_org_via_publisher_is_false(self):
        html = make_html(publisher="黔东南苗族侗族自治州人民政府门户网",
                         declaration="来源：黔东南州人民政府")
        result = source_meta.extract(html, "www.qdn.gov.cn",
                                     "https://www.qdn.gov.cn/a.html")
        self.assertIs(result["is_reprint"], False)

    def test_same_org_via_title_is_false(self):
        html = make_html(title="一碗地道贵州凯里酸汤的好品质，从何而来？_央广网",
                         declaration="来源：央广网")
        self.assertIs(
            source_meta.extract(html, "gz.cnr.cn", "https://gz.cnr.cn/a.html")["is_reprint"],
            False)

    def test_cross_org_with_title_identity_is_true(self):
        # 食品机械设备网 转载 中国食品报
        html = make_html(
            title="苗家酸汤鱼预制菜出富民新路_地方动态_食品机械设备网",
            declaration="来源：中国食品报")
        self.assertIs(
            source_meta.extract(html, "www.foodjx.com",
                                "https://www.foodjx.com/a.html")["is_reprint"], True)

    def test_parent_brand_is_still_a_reprint(self):
        # gz.cri.cn 站点自称「国际在线」，声明「中央广电总台国际在线」→ 跨主体
        html = make_html(title="贵州凯里酸汤海外布局加速-国际在线",
                         declaration="来源：中央广电总台国际在线")
        self.assertIs(
            source_meta.extract(html, "gz.cri.cn", "https://gz.cri.cn/a.html")["is_reprint"],
            True)

    def test_unknown_site_identity_is_none(self):
        # 无 publisher、标题里也没有站点名段 → 无法判断是不是本站自己的稿子
        html = make_html(title="两家行业龙头企业收获亮眼",
                         declaration="来源：贵州日报天眼新闻")
        self.assertIsNone(
            source_meta.extract(html, "dcpp.gog.cn",
                                "http://dcpp.gog.cn/a.shtml")["is_reprint"])

    def test_reprint_label_forces_true(self):
        html = make_html(title="农科院斩获证书-黔东南苗族侗族自治州农业科学院",
                         declaration="转载自黔东南新闻网")
        self.assertIs(
            source_meta.extract(html, "www.qdnnky.com",
                                "http://www.qdnnky.com/a.html")["is_reprint"], True)

    def test_reprint_label_does_not_override_same_org(self):
        # 「转载自本站」只能是本站自己的稿子，转载标签不能盖过同主体证据
        html = make_html(title="一碗酸汤_央广网", declaration="转载自央广网")
        self.assertIs(
            source_meta.extract(html, "gz.cnr.cn", "https://gz.cnr.cn/a.html")["is_reprint"],
            False)

    def test_domain_identity_is_same_org(self):
        # 域名 meituan 出现在声明里（微信公众号“美团Meituan”）→ 同一主体
        html = make_html(declaration="转载自微信公众号“美团Meituan”")
        self.assertIs(
            source_meta.extract(html, "www.meituan.com",
                                "https://www.meituan.com/news/x")["is_reprint"], False)

    def test_no_declaration_falls_back_to_canonical(self):
        different = make_html(canonical="https://a.cn/original.html")
        self.assertIs(
            source_meta.extract(different, "a.cn", "https://a.cn/copy.html")["is_reprint"],
            True)
        same = make_html(canonical="https://a.cn/a.html/")
        self.assertIs(
            source_meta.extract(same, "a.cn", "https://a.cn/a.html")["is_reprint"],
            False)
        self.assertIsNone(
            source_meta.extract(make_html(), "a.cn", "https://a.cn/a.html")["is_reprint"])

    def test_declaration_field_keeps_raw_text(self):
        raw = "来源： \n\t贵州日报天眼新闻"
        result = source_meta.extract(make_html(declaration=raw), "a.cn", "https://a.cn/")
        self.assertIn("贵州日报天眼新闻", result["declaration"])


class TestCleanPublisher(unittest.TestCase):
    """publisher 清洗：不了逗号拼接残渣与标签前缀；取不到返回 None。"""

    def test_comma_residue_is_removed(self):
        self.assertEqual(source_meta.clean_publisher("中国,贵州省人民政府"),
                         "贵州省人民政府")
        self.assertEqual(source_meta.clean_publisher("中国,镇远县人民政府网站"),
                         "镇远县人民政府网站")

    def test_label_prefix_is_removed(self):
        self.assertEqual(source_meta.clean_publisher("来源：贵阳市统计局"),
                         "贵阳市统计局")

    def test_unusable_publisher_is_none(self):
        self.assertIsNone(source_meta.clean_publisher(None))
        self.assertIsNone(source_meta.clean_publisher("中国,"))
        self.assertIsNone(source_meta.clean_publisher("  "))

    def test_extract_returns_clean_publisher(self):
        html = make_html(publisher="中国,贵州省人民政府",
                         declaration="来源：'+SourceName)")
        self.assertEqual(
            source_meta.extract(html, "www.guizhou.gov.cn", "https://x/")["publisher"],
            "贵州省人民政府")


if __name__ == "__main__":
    unittest.main()
