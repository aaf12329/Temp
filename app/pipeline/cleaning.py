"""本地清洗功能：规则检查 / 训练异常模型 / 生成样本质量门槛。

结构：clean_candidates；training_outliers；generated_quality。
调用：workflow/training 聚合入口 → 本模块 → NumPy/sklearn/SciPy。
数据流：原候选 → 保留/排除及理由；真实训练表 → 异常标记；训练/合成表 → 门槛报告。
模型仅拟合显式传入的训练集，不接触验证/测试；异常分数不是事实真伪判定。
"""

import math

import numpy as np
from scipy.stats import wasserstein_distance
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import NearestNeighbors


def clean_candidates(facts: list[dict]) -> tuple[list, list]:
    """规则排除非法或口径未明候选，保留原对象与所有剔除理由。"""
    cleaned, rejected = [], []
    for fact in facts:
        reasons = []
        value = fact.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(
                value) or value < 0:
            reasons.append("指标值不是非负有限数")
        if fact.get("claim_type") != "observation":
            reasons.append("政策目标、预测或其他非实测观测")
        reasons.extend(fact.get("uncertainty_flags") or [])
        if not all(fact.get(name) for name in ("subject", "region", "metric", "unit", "period")):
            reasons.append("缺少年份、主体、地域、指标或单位，无法确定可比口径")
        if not (fact.get("known_at") or fact.get("published_at")):
            reasons.append("缺少明确发布时间")
        target = {**fact, "cleaning_reasons": list(dict.fromkeys(reasons))}
        (rejected if reasons else cleaned).append(target)
    return cleaned, rejected


def training_outliers(frame, config, threads: int) -> tuple[np.ndarray, dict]:
    """只在训练特征拟合 IsolationForest；默认标记不删除，可配置隔离训练异常。"""
    keep = np.ones(len(frame), dtype=bool)
    report = {"model": "IsolationForest", "fit_scope": "training_features_only",
              "status": "skipped", "flagged_indices": [], "removed": 0,
              "reason": "未达到样本门槛或仅启用规则清洗"}
    if config.model == "rules" or len(frame) < config.min_samples:
        return keep, report
    detector = IsolationForest(n_estimators=100, max_samples=min(256, len(frame)),
                               contamination=config.contamination, random_state=42,
                               n_jobs=threads)
    flags = detector.fit_predict(frame) == -1
    if config.remove_training_outliers:
        keep = ~flags
    report.update(status="evaluated", reason=None,
                  flagged_indices=np.flatnonzero(flags).tolist(), removed=int((~keep).sum()),
                  scores=detector.decision_function(frame).tolist(),
                  interpretation="离群只代表统计异常，不证明数据错误；验证/测试样本未删除")
    return keep, report


def generated_quality(real, generated) -> dict:
    """仅以训练集建立尺度、分布和近重复门槛；这是启发式质量筛查，不证明保真。"""
    source = real.to_numpy(dtype=float)
    fake = generated.to_numpy(dtype=float)
    if not len(fake) or not np.isfinite(fake).all():
        return {"accepted": False, "reason": "没有有限的生成样本"}
    low, high = source.min(axis=0), source.max(axis=0)
    scale = np.maximum(high - low, 1e-8)
    within = ((fake >= low - scale * 0.1) & (fake <= high + scale * 0.1)).all(axis=1)
    distances = [wasserstein_distance(source[:, i], fake[:, i]) / scale[i]
                 for i in range(source.shape[1])]
    variable = source.std(axis=0) > 1e-8
    spread = fake.std(axis=0)[variable] / source.std(axis=0)[variable]
    normalized_real = (source - low) / scale
    normalized_fake = (fake - low) / scale
    nearest = NearestNeighbors(n_neighbors=1).fit(normalized_real).kneighbors(
        normalized_fake, return_distance=True)[0][:, 0]
    duplicate_fraction = float((nearest < 1e-5).mean())
    mean_shift = np.abs(fake.mean(axis=0) - source.mean(axis=0)) / np.maximum(
        source.std(axis=0), 1e-8)
    joint_columns = variable & (fake.std(axis=0) > 1e-8)
    correlation_difference = 0.0
    if int(joint_columns.sum()) >= 2:
        correlation_difference = float(np.max(np.abs(
            np.corrcoef(source[:, joint_columns], rowvar=False)
            - np.corrcoef(fake[:, joint_columns], rowvar=False))))
    checks = {"range": bool(within.mean() >= 0.95),
              "distribution": bool(max(distances) <= 0.35),
              "spread": bool(len(spread) and ((spread >= 0.1) & (spread <= 3)).all()),
              "memorization": duplicate_fraction <= 0.1,
              "mean_location": bool(max(mean_shift) <= 0.5),
              "joint_correlation": correlation_difference <= 0.35,
              "sample_count": len(fake) >= 16}
    return {"accepted": all(checks.values()), "checks": checks,
            "normalized_wasserstein": dict(zip(real.columns, map(float, distances))),
            "spread_ratio": spread.tolist(), "in_range_fraction": float(within.mean()),
            "near_duplicate_fraction": duplicate_fraction,
            "mean_shift_in_real_std": dict(zip(real.columns, map(float, mean_shift))),
            "max_correlation_difference": correlation_difference,
            "thresholds": {"max_mean_shift_std": 0.5, "max_correlation_difference": 0.35,
                           "max_normalized_wasserstein": 0.35, "min_generated_rows": 16},
            "fit_scope": "training_only", "heuristic": True,
            "reason": "通过训练分布初筛，仍须真实验证集比较" if all(checks.values())
                      else "合成数据的均值、相关性、分布、范围、多样性或近重复初筛未通过；使用真实数据"}
