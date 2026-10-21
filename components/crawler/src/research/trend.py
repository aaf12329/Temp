# -*- coding: utf-8 -*-
"""按发布时间回测年度指标；纯 CPU、无网络、无落盘，不把新闻数量当产业增长。

结构树：analyze_series → _validate_rows / _analyze_one → _features / _evaluate。
调用关系：入口层传入审核后的同口径年度序列；本模块不导入其它业务模块。
数据流：审核行 → 按 series_id 分组 → 年末可见特征 → 滚动回测 → 可选模型 JSON。
返回契约：{status, series_reports, policy}；每个报告含 series_id、status、requirements。
状态：insufficient_data / invalid_data / dependency_missing / model_error / exploratory_model /
validated。
validated 仅表示留出 MAE 优于上一观测值基线，不能证明产业未来必然增长。
forecast_at 可指定 ISO 日期；默认采用输入中最晚 known_at，所有运算限于此时可见数据。
每折预测 origin+1 年；训练标签必须 target<=origin 且 known_at<=当年年末。
特征只使用历史预测起点当时已发布的连续三个年度，允许公布滞后并显式记录 age。
不填补缺年、不混口径、不调参；稀疏序列仅返回缺口，绝不生成替代训练数据。
最新可见年度距预测起点超过两年时视为陈旧，不向远期外推。
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

FEATURE_NAMES = ["latest_value", "previous_value", "older_value", "change", "prior_change",
                 "available_period_age"]
DEFAULTS = {"min_periods": 20, "min_train_samples": 10, "min_test": 4}
MAX_PUBLICATION_LAG_YEARS = 2
MODEL_PARAMS = {"n_estimators": 120, "max_depth": 2, "learning_rate": 0.05,
                "subsample": 1.0, "colsample_bytree": 1.0, "reg_lambda": 10.0,
                "objective": "reg:squarederror", "tree_method": "hist", "n_jobs": 4,
                "random_state": 42, "verbosity": 0}


# ==================== ① 纯逻辑：校验、历史可见特征、指标 ====================


def _timestamp(value: Any) -> datetime:
    """ISO 日期按 UTC 比较；无时区日期/时间统一视为 UTC。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("known_at / forecast_at 必须是明确的 ISO 发布日期")
    stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(
        timezone.utc)


def _year_end(year: int) -> datetime:
    """完整包含当年 12 月 31 日的所有发布时间。"""
    return datetime(year, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)


def _validate_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """拒绝重复年度、负数、非有限值以及混合口径，避免静默拼接错误趋势。"""
    cleaned: list[dict] = []
    errors: list[str] = []
    for index, row in enumerate(rows):
        try:
            period = row.get("period")
            valid_period = isinstance(period, int) and not isinstance(period, bool)
            if not valid_period or not 1900 <= period <= 9997:
                raise ValueError("period 必须是 1900..9997 内的年度整数")
            raw_value = row.get("value")
            if isinstance(raw_value, bool):
                raise ValueError("value 不能是布尔值")
            value = float(raw_value)
            if not math.isfinite(value) or value < 0:
                raise ValueError("value 必须是非负有限数")
            known = _timestamp(row.get("known_at"))
            if known.year < period:
                raise ValueError("known_at 早于指标年度；请核对发布时间")
            scope = row.get("scope")
            if not isinstance(scope, str) or not scope.strip():
                raise ValueError("scope 必须明确记录口径、地域、单位")
            cleaned.append({"period": period, "value": value, "known": known,
                            "scope": scope.strip()})
        except (TypeError, ValueError, OverflowError) as exc:
            errors.append(f"row[{index}]: {exc}")
    periods = [row["period"] for row in cleaned]
    if len(set(periods)) != len(periods):
        errors.append("同一 series_id 存在重复年度；请先审核去重或单独管理修订版本")
    if len({row["scope"] for row in cleaned}) > 1:
        errors.append("同一 series_id 的 scope 不一致；请按口径、地域、单位拆成独立序列")
    return sorted(cleaned, key=lambda row: row["period"]), errors


def _features(rows: list[dict], origin: int, as_of: datetime) -> dict | None:
    """在给定起点取当时已经公布的连续三年，不利用后来的发布或修订。"""
    available = {row["period"]: row for row in rows
                 if row["period"] <= origin and row["known"] <= as_of}
    if not available:
        return None
    latest = max(available)
    if origin - latest > MAX_PUBLICATION_LAG_YEARS:
        return None
    periods = [latest, latest - 1, latest - 2]
    if not all(period in available for period in periods):
        return None
    values = [available[period]["value"] for period in periods]
    return {"x": [*values, values[0] - values[1], values[1] - values[2], origin - latest],
            "baseline": values[0], "feature_periods": periods,
            "feature_known_at": [available[period]["known"].isoformat() for period in periods]}


def _metrics(actual: list[float], predicted: list[float], anchors: list[float]) -> dict:
    """零值可参与 MAE/RMSE；方向以预测时最后可见值为共同基准。"""
    count = len(actual)
    errors = [prediction - value for value, prediction in zip(actual, predicted)]
    signs = lambda value: (value > 0) - (value < 0)
    directions = [signs(value - anchor) == signs(prediction - anchor)
                  for value, prediction, anchor in zip(actual, predicted, anchors)]
    scale = max(abs(error) for error in errors)
    rmse = 0.0
    if scale:
        rmse = scale * math.sqrt(sum((error / scale) ** 2 for error in errors) / count)
    return {"mae": sum(abs(error) / count for error in errors), "rmse": rmse,
            "direction_accuracy": sum(directions) / count, "n": count}


def _requirements(rows: list[dict], config: dict) -> list[str]:
    """为每条序列说明数据门槛；严格连续，缺年直接阻断建模。"""
    missing: list[str] = []
    if len(rows) < config["min_periods"]:
        missing.append(f"需要至少 {config['min_periods']} 个同口径年度，目前 {len(rows)} 个")
    if rows:
        absent = sorted(set(range(rows[0]["period"], rows[-1]["period"] + 1))
                        - {row["period"] for row in rows})
        if absent:
            missing.append("年度不连续，缺少：" + ", ".join(map(str, absent)))
    return missing


# ==================== ② 模型计算：延迟导入、固定 CPU 参数、无 I/O ====================


def _new_model() -> Any:
    """只在数据够用时加载 XGBoost；模型原始 JSON 交入口层统一保存。"""
    from xgboost import XGBRegressor

    return XGBRegressor(**MODEL_PARAMS)


def _fit_model(samples: list[dict]) -> Any:
    """固定参数训练，不进行超参搜索或随机切分。"""
    model = _new_model()
    model.fit([sample["x"] for sample in samples], [sample["y"] for sample in samples])
    return model


def _predict(model: Any, features: list[float]) -> float:
    """非负指标预测截断至零；非有限模型输出立即报错。"""
    prediction = float(model.predict([features])[0])
    if not math.isfinite(prediction):
        raise ValueError("模型产生非有限预测，请检查输入数值尺度")
    return max(0.0, prediction)


def _examples(rows: list[dict], as_of: datetime) -> list[dict]:
    """每个例子的特征在自身历史起点构造；标签另保留真实公布时间。"""
    samples: list[dict] = []
    for row in rows:
        origin = row["period"] - 1
        features = _features(rows, origin, min(_year_end(origin), as_of))
        if features is not None:
            samples.append({**features, "origin": origin, "target_period": row["period"],
                            "known": row["known"], "y": row["value"]})
    return samples


def _train_available(samples: list[dict], origin: int, as_of: datetime) -> list[dict]:
    """标签目标年和实际发布日期双重闸门，禁止未来标签进入任何一折。"""
    return [sample for sample in samples
            if sample["target_period"] <= origin and sample["known"] <= as_of]


def _evaluate(samples: list[dict], eligible: list[dict]) -> list[dict]:
    """扩展窗口滚动起点回测；仅使用最后 min_test 个可验证起点。"""
    folds: list[dict] = []
    for test in eligible:
        origin = test["origin"]
        cutoff = _year_end(origin)
        training = _train_available(samples, origin, cutoff)
        model = _fit_model(training)
        folds.append({"origin": origin, "forecast_at": cutoff.isoformat(),
                      "target_period": test["target_period"], "actual": test["y"],
                      "actual_known_at": test["known"].isoformat(),
                      "prediction": _predict(model, test["x"]), "baseline": test["baseline"],
                      "feature_periods": test["feature_periods"],
                      "feature_known_at": test["feature_known_at"],
                      "train_target_periods": [sample["target_period"] for sample in training],
                      "train_known_at": [sample["known"].isoformat() for sample in training]})
    return folds


# ==================== ③ 主流程：逐序列报告、门槛判定、自测 ====================


def _analyze_one(series_id: str, raw_rows: list[dict], config: dict) -> dict:
    """独立处理一个序列；不满足门槛时不调用模型。"""
    report: dict = {"series_id": series_id, "status": "insufficient_data", "requirements": [],
                    "input_count": len(raw_rows), "folds": []}
    rows, errors = _validate_rows(raw_rows)
    if errors:
        return {**report, "status": "invalid_data", "requirements": errors}
    if not rows:
        return {**report, "requirements": ["没有可用的审核后年度记录"]}
    as_of = _timestamp(config["forecast_at"]) if config.get("forecast_at") else max(
        row["known"] for row in rows)
    visible = [row for row in rows if row["known"] <= as_of]
    report.update({"scope": rows[0]["scope"], "forecast_at": as_of.isoformat(),
                   "available_count": len(visible),
                   "future_rows_excluded": len(rows) - len(visible)})
    missing = _requirements(visible, config)
    samples = _examples(visible, as_of)
    eligible = [sample for sample in samples if len(_train_available(
        samples, sample["origin"], _year_end(sample["origin"]))) >= config["min_train_samples"]]
    report["available_backtest_folds"] = len(eligible)
    if len(eligible) < config["min_test"]:
        missing.append(f"需要 {config['min_test']} 个滚动留出起点，目前 {len(eligible)} 个；"
                       f"每折至少 {config['min_train_samples']} 个当时已发布的训练标签")
    current = _features(visible, as_of.year, as_of)
    if current is None:
        missing.append("预测起点需要三个连续且已经公布的年度指标，最新年度距起点不得超过两年")
    training = _train_available(samples, as_of.year, as_of)
    if len(training) < config["min_train_samples"]:
        missing.append(f"当前可用训练样本 {len(training)} 个，需 {config['min_train_samples']} 个")
    if missing:
        return {**report, "requirements": missing}
    try:
        folds = _evaluate(samples, eligible[-config["min_test"]:])
        actual = [fold["actual"] for fold in folds]
        anchors = [fold["baseline"] for fold in folds]
        model_metrics = _metrics(actual, [fold["prediction"] for fold in folds], anchors)
        baseline_metrics = _metrics(actual, anchors, anchors)
        beats_baseline = model_metrics["mae"] < baseline_metrics["mae"]
        final_model = _fit_model(training)
        prediction = _predict(final_model, current["x"])
        # 仅通过基线门槛才提供可持久化模型；探索结果仍提供完整回测便于复核。
        raw_model = None
        if beats_baseline:
            raw_model = final_model.get_booster().save_raw(raw_format="json")
    except ImportError as exc:
        return {**report, "status": "dependency_missing",
                "requirements": [f"请安装 requirement.txt 中的建模依赖：{exc}"]}
    except (ValueError, RuntimeError, OverflowError) as exc:
        return {**report, "status": "model_error",
                "requirements": [f"模型未能完成计算，请核对数值尺度及依赖版本：{exc}"]}
    status = "validated" if beats_baseline else "exploratory_model"
    return {**report, "status": status, "folds": folds, "model_metrics": model_metrics,
            "baseline_metrics": baseline_metrics, "beats_baseline": beats_baseline,
            "feature_names": list(FEATURE_NAMES), "model_params": dict(MODEL_PARAMS),
            "train_target_periods": [sample["target_period"] for sample in training],
            "train_known_at": [sample["known"].isoformat() for sample in training],
            "forecast": {"target_period": as_of.year + 1, "value": prediction,
                         "last_value_baseline": current["baseline"],
                         "latest_available_period": current["feature_periods"][0],
                         "feature_periods": current["feature_periods"],
                         "feature_known_at": current["feature_known_at"]},
            "model_json": bytes(raw_model).decode("utf-8") if raw_model is not None else None,
            "interpretation": "单指标的条件预测，仅四个或配置数量的留出起点；"
                              "不是因果判断、置信区间或产业整体增长证明"}


def analyze_series(rows: list[dict], config: dict | None = None) -> dict:
    """分析审核后的年度数值记录；空输入返回可操作缺口，参数错误明确抛出。"""
    settings = {**DEFAULTS, **(config or {})}
    for key in DEFAULTS:
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < DEFAULTS[key]:
            raise ValueError(f"{key} 必须是至少 {DEFAULTS[key]} 的整数，不能降低验证门槛")
    if settings.get("forecast_at"):
        _timestamp(settings["forecast_at"])
    grouped: dict[str, list[dict]] = defaultdict(list)
    invalid: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("series_id"), str):
            invalid.append(f"row[{index}] 缺少字符串 series_id")
            continue
        if not row["series_id"].strip():
            invalid.append(f"row[{index}] series_id 为空")
            continue
        grouped[row["series_id"]].append(row)
    reports = [_analyze_one(series_id, grouped[series_id], settings)
               for series_id in sorted(grouped)]
    statuses = {report["status"] for report in reports}
    status = next(iter(statuses)) if len(statuses) == 1 else "mixed"
    if not reports:
        status = "invalid_data" if invalid else "insufficient_data"
    if invalid and reports:
        status = "mixed"
    return {"status": status, "series_reports": reports,
            "requirements": invalid or ([] if reports else ["请提供审核后的同口径年度序列"]),
            "policy": {"validation": "rolling_origin_publication_as_of", "gpu_required": False,
                       "feature_names": FEATURE_NAMES, **{key: settings[key] for key in DEFAULTS}}}


def _self_test() -> None:
    """纯内存合成空/稀疏输入；不加载模型、不触碰真实数据。"""
    assert analyze_series([])["status"] == "insufficient_data"
    fixture = [{"series_id": "synthetic", "period": 2020, "value": 0.0,
                "known_at": "2021-06-01", "scope": "synthetic only"}]
    assert analyze_series(fixture)["series_reports"][0]["status"] == "insufficient_data"


if __name__ == "__main__":
    _self_test()
    print("trend self-test: OK (synthetic fixtures only)")
