"""时间序列建模显式聚合层，组合旧时点特征、训练清洗、GAN 与绘图。

结构：prepare_split → _fit/_evaluate → train_series；调用：workflow → train_series。
数据流：同口径年度候选 → 发布时点可见样本 → 时间训练/验证/测试 → XGBoost/可选GAN → 报告。
验证选择增强方案后才访问测试标签；GAN/IsolationForest/缩放器只拟合真实训练集。
返回 {status,requirements,split,validation,test,forecast,gan,...}；所有模型均为未人工审核的探索。
"""

import numpy as np
import pandas as pd

from app.common.file_io import write_bytes, write_csv_rows, write_frame, write_json
from app.pipeline.cleaning import training_outliers
from app.pipeline.gan import augment
from app.pipeline.plots import evaluation, missing_plot


def prepare_split(rows, config, forecast_at):
    """复用真实发布时间特征，按时间留出；验证标签在测试起点必须已经公开。"""
    from research import trend

    visible, errors = trend._validate_rows(rows)
    as_of = trend._timestamp(forecast_at)
    visible = [row for row in visible if row["known"] <= as_of]
    requirements = errors + trend._requirements(visible, {"min_periods": config.min_periods})
    samples = trend._examples(visible, as_of)
    test = samples[-config.test_size:]
    if len(test) < config.test_size:
        requirements.append(f"真实测试样本不足 {config.test_size} 个")
    if not test:
        return None, requirements, visible
    test_start = test[0]["origin"]
    pool = [sample for sample in samples if sample["target_period"] <= test_start
            and sample["known"] <= trend._year_end(test_start)]
    validation = pool[-config.validation_size:]
    if len(validation) < config.validation_size:
        requirements.append(f"测试开始前已公布的真实验证样本不足 {config.validation_size} 个")
    if not validation:
        return None, requirements, visible
    validation_start = validation[0]["origin"]
    train = [sample for sample in pool if sample["target_period"] <= validation_start
             and sample["known"] <= trend._year_end(validation_start)]
    if len(train) < config.min_train_samples:
        requirements.append(f"真实训练样本 {len(train)} 个，至少需要 {config.min_train_samples} 个")
    if requirements:
        return None, list(dict.fromkeys(requirements)), visible
    return (train, validation, test), [], visible


def _fit(frame, threads):
    """固定浅树 CPU XGBoost，避免笔记本超参搜索；由 train_series 调用。"""
    from xgboost import XGBRegressor

    model = XGBRegressor(n_estimators=120, max_depth=2, learning_rate=0.05,
                         reg_lambda=10, tree_method="hist", device="cpu", n_jobs=threads,
                         random_state=42, objective="reg:squarederror", verbosity=0)
    model.fit(frame.drop(columns="target"), frame["target"])
    return model


def _evaluate(model, samples, names):
    """只评分调用方传入的真实样本，所有预测检查有限并按非负指标截断。"""
    from research import trend

    predicted = np.maximum(0, model.predict(pd.DataFrame([s["x"] for s in samples], columns=names)))
    if not np.isfinite(predicted).all():
        raise ValueError("XGBoost 输出非有限数值")
    actual, anchors = [s["y"] for s in samples], [s["baseline"] for s in samples]
    return predicted.tolist(), trend._metrics(actual, predicted.tolist(), anchors)


def train_series(rows, config, paths, forecast_at, *, plan=False):
    """逐序列时间验证，必要时请求 GAN；plan 只给出是否需要框架选择。"""
    from research import trend

    split, reasons, visible = prepare_split(rows, config, forecast_at)
    report = {"series_id": rows[0]["series_id"], "scope": rows[0]["scope"],
              "status": "insufficient_data", "requirements": reasons,
              "evidence_status": "machine_candidate_unreviewed", "needs_gan": False,
              "real_rows": len(visible), "gan": {"status": "skipped"}}
    if split is None:
        return report
    train, validation, test = split
    names = trend.FEATURE_NAMES
    frame = pd.DataFrame([s["x"] + [s["y"]] for s in train], columns=names + ["target"])
    keep, cleaning = training_outliers(frame[names], config.cleaning, config.laptop.threads)
    report["cleaning"] = cleaning
    if int(keep.sum()) < config.min_train_samples:
        return {**report, "requirements": ["隔离训练异常后真实训练样本不足"]}
    frame = frame.loc[keep].reset_index(drop=True)
    baseline = _fit(frame, config.laptop.threads)
    real_validation_predictions, real_metrics = _evaluate(baseline, validation, names)
    anchors = [s["baseline"] for s in validation]
    naive_validation = trend._metrics([s["y"] for s in validation], anchors, anchors)
    needs_gan = (config.gan.mode != "off" and len(frame) >= config.gan.min_real_samples
                 and real_metrics["mae"] >= naive_validation["mae"])
    report.update(status="planned", needs_gan=needs_gan,
                  split={"method": "chronological_publication_as_of", "train": len(frame),
                         "validation": len(validation), "test": len(test),
                         "train_target_periods": [s["target_period"] for s, ok in zip(train, keep) if ok],
                         "train_label_known_at": [s["known"].isoformat() for s, ok in zip(train, keep) if ok],
                         "validation_target_periods": [s["target_period"] for s in validation],
                         "test_target_periods": [s["target_period"] for s in test]},
                  validation={"real_only": real_metrics, "naive": naive_validation})
    if plan:
        return report
    paths.root.mkdir(parents=True, exist_ok=True)
    write_frame(paths.file("train"), frame.assign(is_synthetic=False))
    missing_plot(frame, paths.file("missing_plot"))
    for label, samples in [("validation", validation), ("test", test)]:
        table = pd.DataFrame([s["x"] + [s["y"]] for s in samples], columns=names + ["target"])
        table["target_period"] = [s["target_period"] for s in samples]
        table["label_known_at"] = [s["known"].isoformat() for s in samples]
        write_frame(paths.file(label), table.assign(is_synthetic=False))
    selected, selected_name = baseline, "real_only"
    validation_predictions = real_validation_predictions
    if needs_gan:
        generated, gan_report = augment(frame, config.gan, paths, config.laptop.threads)
        report["gan"] = gan_report
        if generated is not None:
            augmented = _fit(pd.concat([frame, generated], ignore_index=True), config.laptop.threads)
            aug_predictions, augmented_metrics = _evaluate(augmented, validation, names)
            report["validation"]["augmented"] = augmented_metrics
            if augmented_metrics["mae"] < real_metrics["mae"]:
                selected, selected_name = augmented, "wgan_augmented"
                validation_predictions = aug_predictions
    else:
        report["gan"]["reason"] = ("真实数据的验证表现已优于末值基线，无需尝试增强"
                                   if real_metrics["mae"] < naive_validation["mae"] else
                                   f"GAN 关闭或真实训练样本不足 {config.gan.min_real_samples} 个")
    # 测试标签直到验证选择完成后才用于评分。所有评估模型保留原始训练截止，不在测试中调参。
    predicted, metrics = _evaluate(selected, test, names)
    test_anchors = [s["baseline"] for s in test]
    naive = trend._metrics([s["y"] for s in test], test_anchors, test_anchors)
    report.update(status="exploratory_model", selected_model=selected_name,
                  model={"name": "XGBoost", "device": "cpu", "n_estimators": 120, "max_depth": 2},
                  test={"selected": metrics, "naive": naive, "beats_naive": metrics["mae"] < naive["mae"]},
                  interpretation="机器候选未人工复核；留出评估仅描述当前样本，不证明因果或预测可靠性")
    current = trend._features(visible, trend._timestamp(forecast_at).year,
                              trend._timestamp(forecast_at))
    report["forecast"] = None
    if current:
        future = float(selected.predict(pd.DataFrame([current["x"]], columns=names))[0])
        if np.isfinite(future):
            report["forecast"] = {"target_period": trend._timestamp(forecast_at).year + 1,
                                  "prediction": max(0.0, future), "baseline": current["baseline"],
                                  "model_training_cutoff": report["split"]["train_target_periods"][-1],
                                  "status": "exploratory_unreviewed"}
    predictions = []
    for label, samples, values in [("validation", validation, validation_predictions),
                                   ("test", test, predicted)]:
        predictions.extend({"split": label, "period": s["target_period"], "actual": s["y"],
                            "prediction": float(value), "baseline": s["baseline"],
                            "actual_known_at": s["known"].isoformat(), "is_synthetic": False}
                           for s, value in zip(samples, values))
    write_csv_rows(paths.file("predictions"), predictions)
    if report["forecast"]:
        write_csv_rows(paths.file("forecast"), [{**report["forecast"], "scope": rows[0]["scope"],
                                               "beats_naive_on_test": metrics["mae"] < naive["mae"]}])
    write_bytes(paths.file("model"), bytes(selected.get_booster().save_raw(raw_format="json")))
    evaluation(predictions, names, selected.feature_importances_, paths, report["forecast"])
    write_json(paths.file("result"), report)
    return report
