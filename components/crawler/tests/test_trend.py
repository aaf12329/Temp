# -*- coding: utf-8 -*-
"""年度趋势测试：全部是显式 synthetic 合成夹具，零网络、零实际数据落盘。

结构树：synthetic_rows / FakeRegressor → TestTrend → unittest.main。
调用关系：仅测试 research.trend 对外接口及防泄漏内部边界。
数据流：内存合成年度序列 → 发布时间闸门 → 审核报告及 JSON 序列化断言。
契约：unittest；正常测试替换模型以验证可见性，真实 XGBoost 由集成冒烟另测。
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from research import trend


# ==================== ① 纯逻辑：合成夹具与确定性假模型 ====================


def synthetic_rows(count: int = 32, delay: int = 1) -> list[dict]:
    """线性合成指标；标签永远写明 synthetic，不当真实产业证据。"""
    return [{"series_id": "synthetic_metric", "period": 1980 + offset,
             "value": float(offset * 10), "known_at": f"{1980 + offset + delay}-06-01",
             "scope": "synthetic fixture only; annual; unit=synthetic"}
            for offset in range(count)]


class FakeRegressor:
    """按最近变化外推的假实现，用于独立检验切分、审计和发布门槛。"""

    def fit(self, x: list[list[float]], y: list[float]) -> FakeRegressor:
        self.train_y = y
        return self

    def predict(self, x: list[list[float]]) -> list[float]:
        return [row[0] + row[3] * (row[5] + 1) for row in x]

    def get_booster(self) -> FakeRegressor:
        return self

    def save_raw(self, raw_format: str) -> bytearray:
        assert raw_format == "json"
        return bytearray(b'{"synthetic_test_model":true}')


class BaselineRegressor(FakeRegressor):
    """与上一观测值完全相等，验证不能仅因成功训练就发布模型。"""

    def predict(self, x: list[list[float]]) -> list[float]:
        return [row[0] for row in x]


# ==================== ② 外部边界：模型调用均由测试替换，无 I/O ====================


class TestTrend(unittest.TestCase):
    """覆盖稀疏/混口径/非有限值、按发布日期回测及模型提升条件。"""

    def test_empty_returns_missing_requirements(self) -> None:
        report = trend.analyze_series([])
        self.assertEqual(report["status"], "insufficient_data")
        self.assertTrue(report["requirements"])

    def test_sparse_does_not_import_or_train_model(self) -> None:
        with patch.object(trend, "_new_model", side_effect=AssertionError("must not train")):
            report = trend.analyze_series(synthetic_rows(5))["series_reports"][0]
        self.assertEqual(report["status"], "insufficient_data")
        self.assertIn("20", " ".join(report["requirements"]))
        self.assertNotIn("forecast", report)

    def test_missing_year_rejects_fabricated_contiguity(self) -> None:
        rows = synthetic_rows()
        del rows[8]
        with patch.object(trend, "_new_model", side_effect=AssertionError("must not train")):
            report = trend.analyze_series(rows)["series_reports"][0]
        self.assertEqual(report["status"], "insufficient_data")
        self.assertIn("1988", " ".join(report["requirements"]))

    def test_nonfinite_negative_and_boolean_values_are_rejected(self) -> None:
        for bad in [float("nan"), float("inf"), -1, True, None]:
            with self.subTest(value=bad):
                rows = synthetic_rows()
                rows[3]["value"] = bad
                report = trend.analyze_series(rows)["series_reports"][0]
                self.assertEqual(report["status"], "invalid_data")
                json.dumps(report, allow_nan=False)

    def test_invalid_dates_and_noninteger_periods_are_rejected(self) -> None:
        for key, value in [("known_at", "uncertain"), ("period", 2000.0),
                           ("period", True), ("known_at", "1900-01-01")]:
            with self.subTest(key=key, value=value):
                rows = synthetic_rows()
                rows[5][key] = value
                report = trend.analyze_series(rows)["series_reports"][0]
                self.assertEqual(report["status"], "invalid_data")

    def test_duplicate_and_mixed_scopes_require_review(self) -> None:
        rows = synthetic_rows()
        rows.append(dict(rows[0]))
        report = trend.analyze_series(rows)["series_reports"][0]
        self.assertEqual(report["status"], "invalid_data")
        rows = synthetic_rows()
        rows[0]["scope"] = "different geography and units"
        self.assertEqual(trend.analyze_series(rows)["status"], "invalid_data")

    def test_default_forecast_uses_latest_publication_and_lag_age(self) -> None:
        with patch.object(trend, "_new_model", side_effect=FakeRegressor):
            report = trend.analyze_series(synthetic_rows())["series_reports"][0]
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["forecast"]["target_period"], 2013)
        self.assertEqual(report["forecast"]["latest_available_period"], 2011)
        self.assertEqual(report["forecast"]["value"], 330.0)
        self.assertEqual(report["model_metrics"]["mae"], 0)
        self.assertEqual(report["model_metrics"]["n"], 4)
        self.assertIsNotNone(report["model_json"])
        json.dumps(report, allow_nan=False)

    def test_every_fold_obeys_target_and_publication_cutoffs(self) -> None:
        rows = synthetic_rows()
        # 延迟公布的标签即便最终已经知道，也不得进入更早的训练起点。
        rows[25]["known_at"] = "2010-06-01"
        with patch.object(trend, "_new_model", side_effect=FakeRegressor):
            report = trend.analyze_series(rows)["series_reports"][0]
        self.assertEqual(len(report["folds"]), 4)
        for fold in report["folds"]:
            cutoff = datetime.fromisoformat(fold["forecast_at"])
            self.assertEqual(fold["target_period"], fold["origin"] + 1)
            self.assertTrue(all(year <= fold["origin"] for year in fold["train_target_periods"]))
            self.assertTrue(all(datetime.fromisoformat(value) <= cutoff
                                for value in fold["train_known_at"]))
            self.assertTrue(all(datetime.fromisoformat(value) <= cutoff
                                for value in fold["feature_known_at"]))
            if fold["origin"] < 2010:
                self.assertNotIn(2005, fold["train_target_periods"])

    def test_unpublished_feature_does_not_change_historical_forecast(self) -> None:
        rows, _ = trend._validate_rows(synthetic_rows())
        as_of = trend._year_end(2000)
        first = trend._features(rows, 2000, as_of)
        rows[20]["value"] = 999999.0  # 2000 年值在 2001 年才发布。
        second = trend._features(rows, 2000, as_of)
        self.assertEqual(first, second)
        self.assertEqual(first["feature_periods"], [1999, 1998, 1997])

    def test_explicit_as_of_excludes_future_publications(self) -> None:
        rows = synthetic_rows()
        with patch.object(trend, "_new_model", side_effect=FakeRegressor):
            first = trend.analyze_series(rows, {"forecast_at": "2008-12-31"})
            rows[-1]["value"] = 987654321.0
            second = trend.analyze_series(rows, {"forecast_at": "2008-12-31"})
        self.assertEqual(first, second)
        report = first["series_reports"][0]
        self.assertGreater(report["future_rows_excluded"], 0)
        self.assertEqual(report["forecast"]["target_period"], 2009)

    def test_baseline_tie_is_exploratory_and_has_no_publishable_model(self) -> None:
        with patch.object(trend, "_new_model", side_effect=BaselineRegressor):
            report = trend.analyze_series(synthetic_rows())["series_reports"][0]
        self.assertEqual(report["status"], "exploratory_model")
        self.assertFalse(report["beats_baseline"])
        self.assertIsNone(report["model_json"])
        self.assertEqual(report["model_metrics"]["mae"], report["baseline_metrics"]["mae"])

    def test_stale_yearly_series_cannot_support_current_forecast(self) -> None:
        with patch.object(trend, "_new_model", side_effect=AssertionError("must not train")):
            report = trend.analyze_series(synthetic_rows(), {"forecast_at": "2026-10-20"})
        self.assertEqual(report["status"], "insufficient_data")
        self.assertIn("两年", " ".join(report["series_reports"][0]["requirements"]))

    def test_missing_dependency_is_actionable(self) -> None:
        with patch.object(trend, "_new_model", side_effect=ImportError("xgboost")):
            report = trend.analyze_series(synthetic_rows())["series_reports"][0]
        self.assertEqual(report["status"], "dependency_missing")
        self.assertIn("requirement.txt", report["requirements"][0])

    def test_scope_series_and_invalid_rows_are_reported_independently(self) -> None:
        rows = synthetic_rows(5)
        other = {**rows[0], "series_id": "second_synthetic"}
        report = trend.analyze_series([*rows, other, {"value": 5}])
        self.assertEqual(report["status"], "mixed")
        self.assertEqual(len(report["series_reports"]), 2)
        self.assertTrue(report["requirements"])

    def test_metrics_handle_zero_and_large_finite_values(self) -> None:
        metrics = trend._metrics([0.0, 1e308], [1e308, 0.0], [0.0, 0.0])
        self.assertTrue(math.isfinite(metrics["mae"]))
        self.assertTrue(math.isfinite(metrics["rmse"]))
        json.dumps(metrics, allow_nan=False)

    def test_validation_thresholds_cannot_be_lowered(self) -> None:
        for key in trend.DEFAULTS:
            with self.subTest(key=key), self.assertRaises(ValueError):
                trend.analyze_series([], {key: 1})

    def test_public_self_test(self) -> None:
        trend._self_test()


# ==================== ③ 主流程：零网络 unittest 入口 ====================


if __name__ == "__main__":
    unittest.main()
