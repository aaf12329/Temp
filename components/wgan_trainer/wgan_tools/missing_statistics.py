"""第三方统计包的缺失诊断适配，不导入其他功能模块。

结构树：pattern_summary / little_test / association_tests / analyze_missing_statistics。
调用树：聚合入口 → analyze_missing_statistics → pandas模式计数、pyampute.MCARTest、SciPy检验。
数据流：删填前原字段表 → 模式、全局探索性MCAR、逐列缺失关联及BY校正 → dict。
契约：返回可JSON序列化dict；不适用/数值退化明确记录，不伪造p值或缺失机制结论。
边界：MCAR非拒绝不证明随机，拒绝不区分MAR/MNAR；填充价值仍由供体和遮盖验证支持。
"""

from importlib.metadata import version
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from pyampute.exploration.mcar_statistical_tests import MCARTest


def pattern_summary(frame: pd.DataFrame) -> dict:
    """用pandas统计精确缺失组合；避免旧pyampute模式API的DataFrame.append兼容问题。"""
    mask = frame.isna()
    # 使用内部唯一名称，兼容用户字段名恰为 count / mdp 等情况。
    mask.columns = [f"c{i}" for i in range(len(frame.columns))]
    counts = mask.value_counts(sort=True, dropna=False)
    patterns = []
    for bits, count in counts.items():
        bits = bits if isinstance(bits, tuple) else (bits,)
        patterns.append({"count": int(count), "fraction": float(count / len(frame)),
                         "missing_columns": [name for name, missing in zip(frame.columns, bits) if missing]})
    return {"engine": "pandas", "unique_count": len(patterns),
            "complete_rows": int((~frame.isna().any(axis=1)).sum()),
            "patterns": patterns[:100], "truncated": len(patterns) > 100}


def little_test(frame: pd.DataFrame, categorical, alpha: float) -> dict:
    """在最多40个非退化数值字段上调用pyampute探索性Little检验。"""
    numeric = frame.drop(columns=list(categorical), errors="ignore").select_dtypes(include="number").astype(float)
    usable = [name for name in numeric if numeric[name].count() >= 20 and numeric[name].nunique() > 1]
    # 优先纳入有缺失的字段；限制维数以避免包内部浮点位编码超过精度范围。
    columns = sorted(usable, key=lambda name: not numeric[name].isna().any())[:40]
    result = {"engine": "pyampute.MCARTest", "status": "not_applicable", "columns": columns,
              "excluded_columns": [name for name in frame if name not in columns],
              "p_value": None, "alpha": alpha, "interpretation": "无法据此判定缺失机制",
              "limitations": "探索性数值子表检验，使用样本均值/成对协方差近似；非显著不证明MCAR，不能区分MAR与MNAR。"}
    table = numeric[columns]
    if len(columns) < 2 or len(table) <= len(columns) + 2 or not table.isna().any().any():
        result["reason"] = "有效数值维度、样本量或变化的缺失模式不足"
        return result
    if table.isna().value_counts().min() < 5:
        result["reason"] = "存在不足5个样本的缺失模式，探索性卡方近似不稳妥"
        return result
    scaled = (table - table.mean()) / table.std(ddof=1)
    covariance = scaled.cov().to_numpy()
    if not np.isfinite(covariance).all():
        result["reason"] = "协方差不可估计，部分字段缺少共同观测"
        return result
    eigenvalues = np.linalg.eigvalsh(covariance)
    if eigenvalues.min() <= 1e-10 or np.linalg.cond(covariance) > 1e10:
        result["reason"] = "协方差奇异、非正定或病态；不强行填充或删维凑出检验结果"
        return result
    scaled.columns = [f"feature_{i}" for i in range(len(columns))]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            p_value = float(MCARTest(method="little")(scaled))
        if not np.isfinite(p_value) or not 0 <= p_value <= 1:
            raise ValueError("包返回非有限或无效p值")
    except (np.linalg.LinAlgError, ValueError, RuntimeWarning, FloatingPointError) as error:
        result.update(status="inconclusive", reason=str(error))
        return result
    result.update(status="evaluated", p_value=p_value, reject_mcar=p_value < alpha,
                  interpretation="拒绝该数值子表的完全随机缺失假设" if p_value < alpha
                  else "未拒绝该数值子表的完全随机缺失假设；不等于证明可填充")
    return result


def association_tests(frame: pd.DataFrame, categorical, groups, alpha: float, min_group_size: int) -> dict:
    """以SciPy检验缺失指示与观测字段的关联；所有有效比较统一BY多重校正。"""
    numeric = frame.drop(columns=list(categorical), errors="ignore").select_dtypes(include="number")
    comparisons, skipped = [], []
    for target in frame:
        missing = frame[target].isna()
        if not missing.any() or missing.all():
            continue
        for predictor in numeric:
            if predictor == target:
                continue
            left = numeric.loc[missing, predictor].dropna().to_numpy(dtype=float)
            right = numeric.loc[~missing, predictor].dropna().to_numpy(dtype=float)
            if min(len(left), len(right)) < min_group_size:
                continue
            pooled = np.sqrt((np.var(left, ddof=1) + np.var(right, ddof=1)) / 2)
            if not np.isfinite(pooled) or pooled <= 1e-12:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                p_value = float(stats.ttest_ind(left, right, equal_var=False).pvalue)
            if np.isfinite(p_value):
                comparisons.append({"column": target, "predictor": predictor, "kind": "numeric",
                    "test": "scipy.stats.ttest_ind:welch", "p_value": p_value,
                    "effect": float(abs(left.mean() - right.mean()) / pooled), "effect_threshold": 0.5,
                    "outside_observed_range_fraction": float(np.mean((left < right.min()) | (left > right.max()))),
                    "observed_counts": [len(left), len(right)]})
        for predictor in dict.fromkeys(groups):
            if predictor == target or predictor not in frame:
                continue
            # 显式把分组键缺失当作一组，保留结构性缺失证据。
            codes, _ = pd.factorize(frame[predictor], use_na_sentinel=False)
            table = pd.crosstab(codes, missing).reindex(columns=[False, True], fill_value=0)
            counts = table.to_numpy()
            if not 2 <= len(table) <= 50 or (counts.sum(axis=1) < min_group_size).any():
                skipped.append({"column": target, "predictor": predictor, "reason": "类别数或分组样本量不适合检验"})
                continue
            chi2, _, _, expected = stats.chi2_contingency(counts, correction=False)
            if counts.shape == (2, 2):
                p_value = float(stats.fisher_exact(counts).pvalue)
                method = "scipy.stats.fisher_exact"
            elif (expected < 5).any():
                skipped.append({"column": target, "predictor": predictor, "reason": "卡方期望频数不足5"})
                continue
            else:
                p_value = float(stats.chi2_contingency(counts, correction=False).pvalue)
                method = "scipy.stats.chi2_contingency"
            comparisons.append({"column": target, "predictor": predictor, "kind": "categorical",
                "test": method, "p_value": p_value, "effect": float(np.sqrt(chi2 / counts.sum())),
                "effect_threshold": 0.3, "group_counts": counts.sum(axis=1).tolist()})
    if comparisons:
        corrected = stats.false_discovery_control([item["p_value"] for item in comparisons], method="by")
        for item, q_value in zip(comparisons, corrected):
            item["q_value"] = float(q_value)
            item["strong_association"] = bool(q_value < alpha and item["effect"] >= item["effect_threshold"])
    return {"engine": "scipy.stats", "correction": "Benjamini-Yekutieli (BY)",
            "alpha": alpha, "comparisons": comparisons, "skipped": skipped,
            "scope": "只使用比较字段可观测的样本；关联不是因果或缺失机制证明"}


def analyze_missing_statistics(frame: pd.DataFrame, categorical=(), groups=(), *, alpha=0.05,
                               min_group_size=5) -> dict:
    """返回真实第三方检验与缺失模式；入口将结果送入自动决策。"""
    if frame.empty or frame.columns.has_duplicates:
        raise ValueError("缺失统计需要非空、字段名唯一的表。")
    if not 0 < alpha < 1 or type(min_group_size) is not int or min_group_size < 2:
        raise ValueError("alpha应位于(0,1)，min_group_size至少为2。")
    return {"versions": {name: version(name) for name in ("pyampute", "scipy", "pandas")},
            "patterns": pattern_summary(frame), "little_mcar": little_test(frame, categorical, alpha),
            "associations": association_tests(frame, categorical, groups, alpha, min_group_size)}


if __name__ == "__main__":
    sample = pd.DataFrame({"x": [1., np.nan, 3.], "y": [2., 4., 6.]})
    assert analyze_missing_statistics(sample)["patterns"]["unique_count"] == 2
    print("Missing statistics self-check passed")
