"""离线图表功能，不导入其他业务模块。

结构：missing_plot / data_quality_plot / evaluation / gan_plot；调用：聚合入口 → matplotlib。
数据流：实际质量计数/真实留出预测/训练损失 → navy/pink 图表 → 共用 I/O PNG。
图表参考 WGAN_Boost_Xgboost 的实际值/预测值、残差及重要性布局，所有图来自实际计算。
"""

import numpy as np

from app.common.file_io import save_figure, write_csv_rows, write_json


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


def data_quality_plot(report, destination):
    """展示真实阶段计数与字段缺失率，空集合不生成虚假的完整率或模型性能。"""
    plt = _pyplot()
    figure, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    names = ["raw_candidates", "organized_candidates", "usable_candidates", "review_records", "annual_rows"]
    labels = ["Raw rule\ncandidates", "Organized\ncandidates", "Usable\ncandidates", "Review\nrecords", "Annual\nrows"]
    values = [report["counts"][key] for key in names]
    axes[0].bar(labels, values, color=["#8698af", "#223c64", "#4f8777", "#df779c", "#223c64"])
    axes[0].set(title="Actual record counts by stage", ylabel="Records", ylim=(0, max(values + [1]) * 1.18))
    for index, value in enumerate(values):
        axes[0].text(index, value, str(value), ha="center", va="bottom")
    fields = list(report["before"]["fields"])
    index = np.arange(len(fields))
    has_rows = False
    for offset, (stage, label, color) in enumerate([
        ("before", "Before", "#8698af"), ("after", "Organized", "#223c64"),
        ("usable", "Usable", "#df779c")]):
        size = report[stage]["rows"]
        if size:
            rates = [report[stage]["fields"][field]["missing_fraction"] for field in fields]
            axes[1].bar(index + (offset - 1) * 0.24, rates, width=0.24, color=color,
                        label=f"{label} (n={size})")
            has_rows = True
    axes[1].set(title="Missing fields (each stage has its own denominator)", ylabel="Missing fraction", ylim=(0, 1.08))
    axes[1].set_xticks(index, labels=fields, rotation=30, ha="right")
    if has_rows:
        axes[1].legend(fontsize=8)
    else:
        axes[1].text(0.5, 0.5, "No candidate rows to profile", ha="center", transform=axes[1].transAxes)
    figure.text(0.5, 0.015, "Completeness is not factual accuracy. Organized candidates are not new observations.",
                ha="center", fontsize=9)
    figure.tight_layout(rect=(0, 0.055, 1, 1))
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


def comparison_plot(metrics, destination):
    """同一真实测试集分别展示MAE/RMSE/R²，量纲不混在一张坐标轴。"""
    plt = _pyplot()
    figure, axes = plt.subplots(1, 3, figsize=(13, 4))
    for axis, name in zip(axes, ("mae", "rmse", "r2")):
        labels = [label for label, values in metrics.items() if values.get(name) is not None]
        axis.bar(labels, [metrics[label][name] for label in labels], color="#223c64")
        axis.set_title(name.upper())
        axis.tick_params(axis="x", rotation=25)
    figure.suptitle("Independent real test set; synthetic data excluded")
    figure.tight_layout()
    save_figure(destination, figure)
    plt.close(figure)


def shap_plot(names, values, destination):
    """绘制XGBoost原生TreeSHAP绝对贡献，不把模型解释当成因果关系。"""
    plt = _pyplot()
    figure, axis = plt.subplots(figsize=(8, 4))
    order = np.argsort(values)
    axis.barh(np.asarray(names)[order], np.asarray(values)[order], color="#223c64")
    axis.set(xlabel="Mean absolute TreeSHAP contribution", title="Real test features; not causality")
    figure.tight_layout()
    save_figure(destination, figure)
    plt.close(figure)


def gan_tsne(real, generated, paths, *, accepted: bool, max_points=2000, threads=None, seed=42,
             max_iter=1000, perplexity=None):
    """训练数据拟合尺度，固定随机抽样后联合降维真实/生成样本；不接收验证和测试。"""
    from sklearn.manifold import TSNE
    from sklearn.preprocessing import MinMaxScaler
    from threadpoolctl import threadpool_limits

    metadata = {"status": "skipped", "fit_scope": "real_training_only",
                "input_real_rows": len(real), "input_synthetic_rows": len(generated),
                "max_points": max_points, "seed": seed, "quality_accepted": accepted,
                "interpretation": "联合t-SNE仅作探索，点云重叠不证明生成质量或产业事实真实"}
    if real.shape[1] < 2 or len(real) < 2 or len(generated) < 2:
        metadata["reason"] = "需要至少2个数值字段、2条真实训练数据和2条生成数据"
        write_json(paths.file("tsne_meta"), metadata)
        return metadata
    rng = np.random.default_rng(seed)
    real_ids = np.sort(rng.choice(len(real), min(len(real), max_points // 2), replace=False))
    fake_ids = np.sort(rng.choice(len(generated), min(len(generated), max_points // 2), replace=False))
    # 缩放器只拟合原始真实训练表，抽样不会引入测试信息，也不让生成分布决定尺度。
    scaler = MinMaxScaler(feature_range=(-1, 1)).fit(real)
    combined = np.vstack([scaler.transform(real.iloc[real_ids]),
                          scaler.transform(generated.iloc[fake_ids])])
    if not np.isfinite(combined).all():
        metadata["reason"] = "存在非有限值，不能绘制t-SNE"
        write_json(paths.file("tsne_meta"), metadata)
        return metadata
    if not np.any(np.ptp(combined, axis=0) > 1e-12):
        metadata["reason"] = "所有字段均为常量，没有可解释的分布结构"
        write_json(paths.file("tsne_meta"), metadata)
        return metadata
    actual_perplexity = 30.0 if perplexity is None else perplexity
    if actual_perplexity >= len(combined):
        if perplexity is not None:
            raise ValueError("JSON 中的 t-SNE perplexity 必须小于抽样总数。")
        actual_perplexity = max(1.0, (len(combined) - 1) / 3)
    with threadpool_limits(limits=threads):
        points = TSNE(n_components=2, perplexity=actual_perplexity, max_iter=max_iter,
                      init="pca", learning_rate="auto", random_state=seed,
                      method="barnes_hut", n_jobs=threads).fit_transform(combined)
    rows = []
    for i, (source, ids) in enumerate([("real_train", real_ids), ("synthetic", fake_ids)]):
        offset = 0 if i == 0 else len(real_ids)
        rows.extend({"source": source, "source_row": int(row), "is_synthetic": bool(i),
                     "tsne_1": float(points[offset + j, 0]),
                     "tsne_2": float(points[offset + j, 1])} for j, row in enumerate(ids))
    write_csv_rows(paths.file("tsne_points"), rows)
    plt = _pyplot()
    figure, axis = plt.subplots(figsize=(8, 6))
    for start, stop, color, marker, label in [
            (0, len(real_ids), "#223c64", "o", "Real training"),
            (len(real_ids), len(points), "#df779c", "s",
             "Synthetic (initial checks passed)" if accepted else "Synthetic (initial checks failed)")]:
        axis.scatter(points[start:stop, 0], points[start:stop, 1], color=color,
                     marker=marker, alpha=0.7, s=24, label=label)
    axis.set(xlabel="t-SNE 1", ylabel="t-SNE 2", title="WGAN-GP: real training vs synthetic")
    axis.legend()
    axis.text(0.5, -0.13, "Exploratory visualization; overlap does not establish data quality",
              transform=axis.transAxes, ha="center", fontsize=9)
    figure.tight_layout()
    save_figure(paths.file("tsne"), figure)
    plt.close(figure)
    metadata.update(status="completed", plotted_real=len(real_ids), plotted_synthetic=len(fake_ids),
                    perplexity=actual_perplexity, max_iter=max_iter, method="barnes_hut",
                    scaling="MinMaxScaler(-1,1), fit real training only",
                    real_row_indices=real_ids.tolist(), synthetic_row_indices=fake_ids.tolist())
    write_json(paths.file("tsne_meta"), metadata)
    return metadata
