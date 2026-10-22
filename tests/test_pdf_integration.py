"""资料融合的隔离验收，不读取用户PDF或真实网页，不调用任何模型API。

结构：HTML字段定位 → 内容寻址晋升 → 候选质量统计 → 原生图表输出。
调用：pytest → 正式纯模块/适配层；数据流：合成HTML/候选 → 临时来源审计与质量产物。
"""

import copy
import hashlib

from PIL import Image

from app.common.file_io import read_bytes, write_bytes
from app.common.paths import PipelinePaths
from app.crawler.html_metadata import publication_date, recover_metadata
from app.crawler.integrated import IntegratedCrawler
from app.pipeline.data_quality import field_rows, profile_candidates, quality_report, report_lines
from app.pipeline.plots import data_quality_plot


def html_fields(body, *, source=None, title=None):
    """以纯字符串调用正式HTML字段定位，无网络与磁盘副作用。"""
    return recover_metadata(body.encode("utf-8"), "utf-8", source or {}, title)


def fact(**changes):
    """构造字段完整的合成候选，数值不是产业事实。"""
    return {"fact_id": "synthetic", "subject": "餐饮业", "region": "榕城", "metric": "revenue",
            "period": "2023", "period_type": "year", "value": 0.0, "unit": "元",
            "known_at": "2024-03-01", "record_id": "one", "claim_type": "observation",
            "uncertainty_flags": [], **changes}


def test_publication_label_and_h1_recovery_keep_quotes():
    """跨span的发布标签可定位，原文年度不成为发布时间，h1只补缺失标题。"""
    body = '<html><body><h1>2023年榕城统计公报</h1><p>发布时间：<span>2024年3月1日</span></p></body></html>'
    fields, audit = html_fields(body)
    assert fields == {"published_at": "2024-03-01", "title": "2023年榕城统计公报"}
    evidence = audit["recovered"][0]["evidence"]
    assert any("2024年3月1日" in item["raw"] for item in evidence)


def test_publication_attributes_and_historical_dates():
    """属性顺序/大小写、历史年份和全角日期有效，日历无效值被拒绝。"""
    fields, _ = html_fields('<meta CONTENT="1999年2月28日" NAME="datePublished"/>')
    assert fields["published_at"] == "1999-02-28"
    fields, _ = html_fields('<time itemprop="datePublished" datetime="2024-03-01T09:30:00+08:00"></time>')
    assert fields["published_at"] == "2024-03-01T09:30:00+08:00"
    assert publication_date("２０２４年３月１日") == "2024-03-01"
    assert publication_date("2024-02-30") is None
    assert publication_date("2023年") is None


def test_conflicting_dates_never_pick_first_and_valid_existing_fields_win():
    """有冲突的DOM日期不随意选择，原有明确有效字段和标题保持。"""
    body = '<p>发布时间：2024-03-01</p><p>发布日期：2024-03-02</p><h1>备用标题</h1>'
    fields, audit = html_fields(body)
    assert "published_at" not in fields and audit["issues"]
    fields, _ = html_fields(body, source={"published_at": "2024-02-01"}, title="已有标题")
    assert fields == {}


def test_script_footer_update_and_measurement_year_do_not_supply_publication():
    """脚本、页脚、更新时间、计划日期和正文测量年都不能充当发布时点。"""
    body = '''<div><script>发布时间：2024-03-01</script></div>
    <footer>发布时间：2024-03-02</footer><p>更新时间：2024-03-03</p>
    <p>预计发布时间：2024-03-04</p><p>2023年餐饮收入12亿元。</p>'''
    fields, _ = html_fields(body)
    assert "published_at" not in fields
    fields, _ = html_fields('<time datetime="2024-03-01">活动日期</time>')
    assert "published_at" not in fields


def test_raw_html_promotion_is_immutable_and_audits_recovered_fields(tmp_path):
    """实际晋升时补来源字段，存档字节/哈希保持原样，记录仍指向相对路径。"""
    workspace = tmp_path / "workspace"
    raw = workspace / "data/raw/html/fixture.html"
    content = '<h1>合成统计公报</h1><p class="pub-time">2024年3月1日</p>'.encode()
    write_bytes(raw, content)
    record = {"record_id": "synthetic", "verdict": "static", "status": "ok", "source": {},
              "meta": {"code_version": "test"}, "storage": {"raw_path": "data/raw/html/fixture.html"}}
    root = tmp_path / "data"
    IntegratedCrawler(root)._promote(record, workspace)
    assert record["source"]["published_at"] == "2024-03-01"
    assert record["meta"]["source_extraction"]["recovered"]
    target = root / record["storage"]["raw_path"].removeprefix("data/")
    assert read_bytes(target) == read_bytes(raw) == content
    assert hashlib.sha256(content).hexdigest() in record["storage"]["raw_path"]
    assert not target.is_relative_to(workspace)


def test_missing_invalid_and_zero_are_distinct_without_mutation():
    """零是有效数值；缺失/格式错误分开统计，不修改或填充候选。"""
    rows = [fact(), fact(value=None, region=" "), fact(value="不是数字", known_at="2024-02-30")]
    original = copy.deepcopy(rows)
    profile = profile_candidates(rows)
    assert profile["fields"]["value"] == {"label": "数值", "missing": 1, "invalid": 1,
                                           "missing_fraction": 1 / 3, "valid": 1}
    assert profile["fields"]["known_at"]["invalid"] == 1
    assert rows == original
    empty = profile_candidates([])
    assert all(field["missing_fraction"] is None for field in empty["fields"].values())


def test_duplicate_profile_never_merges_different_scope_date_or_year():
    """重复统计不把不同地域、年份、单位、发布时点或统计范围混为同一条。"""
    rows = [fact(), fact(record_id="reprint"), fact(period="2022"), fact(region="其他市"),
            fact(known_at="2024-04-01"), fact(scope="qualified"), fact(unit="万元")]
    profile = profile_candidates(rows)
    assert profile["exact_duplicate_candidates"] == 1
    assert len(rows) == 7


def test_report_has_separate_stage_denominators_and_review_blocks():
    """LLM增加候选和待复核原文块时，阶段计数不冒充留存率或新增真实样本。"""
    before = profile_candidates([fact(region=None)])
    usable = [fact()]
    rejected = [{**fact(region=None), "cleaning_reasons": ["missing_region", "missing_region"]}]
    report = quality_report(before, usable + rejected, usable, rejected, [{"period": 2023}],
                            rejected + [{"text": "未处理块"}], llm_status="partial")
    assert report["before"]["rows"] == 1 and report["after"]["rows"] == 2
    assert report["counts"]["review_records"] == 2 and report["counts"]["annual_rows"] == 1
    assert report["rejection_reasons"] == [{"code": "missing_region", "count": 1}]
    assert len(field_rows(report)) == 21
    assert "待复核记录|2" in "\n".join(report_lines(report))


def test_quality_png_and_empty_chart_are_actual_valid_images(tmp_path):
    """有/无候选均输出真实阶段统计，PNG尺寸与数据相符，零条不假画完整率。"""
    for name, rows in [("populated", [fact()]), ("empty", [])]:
        paths = PipelinePaths(tmp_path / name, organized=True)
        report = quality_report(profile_candidates(rows), rows, rows, [], [], [], llm_status="disabled")
        data_quality_plot(report, paths.file("data_quality_plot"))
        with Image.open(paths.file("data_quality_plot")) as picture:
            assert picture.width >= 1000 and picture.height >= 400
            picture.verify()
        assert paths.file("data_quality") in paths.registered_files()
        assert paths.file("data_quality_fields") in paths.registered_files()
