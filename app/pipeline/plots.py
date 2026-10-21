"""离线图表功能，不导入其他业务模块。

结构：missing_plot / evaluation / gan_plot；调用：聚合入口 → 本模块 → matplotlib。
数据流：真实留出预测/训练损失 → navy/pink 图表 → 共用 I/O PNG。
图表参考 WGAN_Boost_Xgboost 的实际值/预测值、残差及重要性布局，所有图来自实际计算。
"""

import numpy as np

from app.common.file_io import save_figure


def _pyplot():
    """使用无窗口后端，由各图表函数调用，不启动 GUI。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def missing_plot(frame, destination):
    """输出训练原字段缺失率；空输入不伪造图表。"""
    if frame.empty:
        return
    plt = _pyplot()
    figure, axis = plt.subplots(figsize=(9, 4))
    axis.bar(np.arange(len(frame.columns)), frame.isna().mean().to_numpy(), color="#223c64")
    axis.set(ylim=(0, 1), ylabel="Missing fraction", title="Observed training data quality")
    axis.set_xticks(np.arange(len(frame.columns)), labels=list(frame.columns), rotation=35, ha="right")
    figure.tight_layout()
    save_figure(destination, figure)
    plt.close(figure)


def evaluation(predictions, feature_names, importance, paths, forecast=None):
    """真实 validation/test 散点、残差、误差分布、特征重要性与年度预测图。"""
    plt = _pyplot()
    figure, axes = plt.subplots(1, 3, figsize=(14, 4.3))
    for subset, color, marker in [("validation", "#223c64", "o"), ("test", "#df779c", "s")]:
        rows = [row for row in predictions if row["split"] == subset]
        actual = np.array([row["actual"] for row in rows])
        predicted = np.array([row["prediction"] for row in rows])
        axes[0].scatter(actual, predicted, color=color, marker=marker, label=subset)
        axes[1].scatter(predicted, actual - predicted, color=color, marker=marker, label=subset)
        axes[2].hist(actual - predicted, bins=min(5, len(rows)), color=color, alpha=0.65,
                     label=subset)
    values = [float(row[key]) for row in predictions for key in ("actual", "prediction")]
    low, high = min(values), max(values)
    axes[0].plot([low, high], [low, high], "--", color="#777777")
    axes[0].set(xlabel="Actual", ylabel="Prediction", title="Real holdout observations")
    axes[1].axhline(0, color="#777777", linestyle="--")
    axes[1].set(xlabel="Prediction", ylabel="Actual - prediction", title="Residuals")
    axes[2].set(xlabel="Residual", ylabel="Count", title="Residual distribution")
    for axis in axes:
        axis.legend()
        axis.grid(alpha=0.15)
    figure.tight_layout()
    save_figure(paths.file("evaluation"), figure)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8, 4))
    order = np.argsort(importance)
    axis.barh(np.array(feature_names)[order], np.array(importance)[order], color="#223c64")
    axis.set(xlabel="XGBoost gain-based importance", title="Feature importance (not causality)")
    figure.tight_layout()
    save_figure(paths.file("importance"), figure)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(9, 4))
    test = [row for row in predictions if row["split"] == "test"]
    years = [row["period"] for row in test]
    axis.plot(years, [row["actual"] for row in test], "o-", color="#223c64", label="Actual")
    axis.plot(years, [row["prediction"] for row in test], "s--", color="#df779c", label="XGBoost")
    axis.plot(years, [row["baseline"] for row in test], ":", color="#777777", label="Last observed")
    if forecast:
        axis.scatter([forecast["target_period"]], [forecast["prediction"]], marker="*", s=140,
                     color="#df779c", label="Future: exploratory / unreviewed")
    axis.set(xlabel="Target year", ylabel="Value", title="Real test years and conditional forecast")
    axis.legend()
    figure.tight_layout()
    save_figure(paths.file("trend"), figure)
    plt.close(figure)


def gan_plot(history, destination):
    """输出实际训练器损失与梯度惩罚曲线，不用损失下降替代质量评价。"""
    plt = _pyplot()
    columns = [name for name in history.columns if name not in {"epoch", "seconds"}]
    figure, axes = plt.subplots(1, 2, figsize=(11, 4))
    for name in columns:
        axis = axes[1] if "gp" in name.lower() or "penalty" in name.lower() else axes[0]
        axis.plot(np.arange(1, len(history) + 1), history[name], label=name)
    for axis in axes:
        axis.set_xlabel("Epoch")
        if axis.lines:
            axis.legend()
    axes[0].set_title("WGAN-GP training diagnostics")
    axes[1].set_title("Gradient penalty")
    figure.tight_layout()
    save_figure(destination, figure)
    plt.close(figure)
