"""时间序列建模显式聚合层，组合旧时点特征、训练清洗、GAN 与绘图。

结构：prepare_split → _fit/_evaluate → train_series；调用：workflow → train_series。
数据流：同口径年度候选 → 发布时点可见样本 → 时间训练/验证/测试 → XGBoost/可选GAN → 报告。
验证选择增强方案后才访问测试标签；GAN/IsolationForest/缩放器只拟合真实训练集。
返回 {status,requirements,split,validation,test,forecast,gan,...}；所有模型均为未人工审核的探索。
"""

import hashlib

import numpy as np
import pandas as pd

from app.common.file_io import (
    read_bytes,
    read_json,
    write_bytes,
    write_csv_rows,
    write_frame,
    write_json,
)
from app.pipeline.cleaning import training_outliers
from app.pipeline.gan import augment
from app.pipeline.plots import comparison_plot, evaluation, missing_plot, shap_plot


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


def _scores(actual, predicted, anchors):
    """统一计算XGBoost和末值基准指标，常量目标的R²标为不适用。"""
    from research import trend
    from sklearn.metrics import r2_score

    metrics = trend._metrics(actual, list(predicted), anchors)
    metrics["r2"] = float(r2_score(actual, predicted)) if len(set(actual)) > 1 else None
    return metrics


def _evaluate(model, samples, names):
    """只评分调用方传入的真实样本，所有预测检查有限并按非负指标截断。"""
    predicted = np.maximum(0, model.predict(pd.DataFrame([s["x"] for s in samples], columns=names)))
    if not np.isfinite(predicted).all():
        raise ValueError("XGBoost 输出非有限数值")
    actual, anchors = [s["y"] for s in samples], [s["baseline"] for s in samples]
    return predicted.tolist(), _scores(actual, predicted.tolist(), anchors)


def train_series(rows, config, paths, forecast_at, *, plan=False, stage="fit", use_generated=False):
    """分阶段：plan仅查门槛，gan只生成供审查，fit按用户确认读取生成表且绝不重训GAN。"""
    from research import trend

    split, reasons, visible = prepare_split(rows, config, forecast_at)
    report = {"series_id": rows[0]["series_id"], "scope": rows[0]["scope"],
              "status": "insufficient_data", "requirements": reasons,
              "evidence_status": "machine_candidate_unreviewed", "needs_gan": False,
              "real_rows": len(visible), "gan": {"status": "skipped",
                  "min_real_samples": config.gan.min_real_samples,
                  "reason": "未启动WGAN：本序列尚未满足真实数据建模门槛，并非训练失败。"}}
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
    needs_gan = config.gan.mode != "off" and len(frame) >= config.gan.min_real_samples
    report["gan"].update(real_train_rows=len(frame), reason=(
        "真实训练量满足GAN门槛，等待用户选择是否训练。" if needs_gan and (plan or stage == "gan") else
        "本轮未训练WGAN，仅使用真实数据进行XGBoost。" if needs_gan else
        "本轮已关闭WGAN，使用真实数据。" if config.gan.mode == "off" else
        f"未启动WGAN：真实训练样本{len(frame)}条，不足{config.gan.min_real_samples}条；可直接用真实数据XGBoost。"))
    report.update(status="planned", needs_gan=needs_gan,
                  split={"method": "chronological_publication_as_of", "train": len(frame),
                         "validation": len(validation), "test": len(test),
                         "train_target_periods": [s["target_period"] for s, ok in zip(train, keep) if ok],
                         "train_label_known_at": [s["known"].isoformat() for s, ok in zip(train, keep) if ok],
                         "validation_target_periods": [s["target_period"] for s in validation],
                         "test_target_periods": [s["target_period"] for s in test]},
                  validation={})
    if plan:
        return report
    frame_hash = hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()
    if stage == "gan":
        if needs_gan:
            _, gan_report = augment(frame, config.gan, paths, config.laptop.threads)
            if paths.file("generated").is_file():
                gan_report["generated_sha256"] = hashlib.sha256(read_bytes(paths.file("generated"))).hexdigest()
            report.update(status="gan_review", gan={**gan_report, "frame_sha256": frame_hash})
        else:
            report.update(status="gan_unavailable", gan={"status": "skipped", "reason":
                          f"GAN关闭或真实训练样本不足{config.gan.min_real_samples}条"})
        write_json(paths.file("gan_review"), report)
        return report
    baseline = _fit(frame, config.laptop.threads)
    real_validation_predictions, real_metrics = _evaluate(baseline, validation, names)
    anchors = [s["baseline"] for s in validation]
    naive_validation = _scores([s["y"] for s in validation], anchors, anchors)
    report["validation"] = {"real_only": real_metrics, "naive": naive_validation}
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
    if paths.file("gan_review").is_file():
        report["gan"] = read_json(paths.file("gan_review"))["gan"]
    report["gan"]["user_accepted"] = bool(use_generated)
    if use_generated and report["gan"].get("status") == "accepted":
        gan_report = report["gan"]
        if (gan_report.get("status") == "accepted" and gan_report.get("frame_sha256") == frame_hash
                and paths.file("generated").is_file()
                and hashlib.sha256(read_bytes(paths.file("generated"))).hexdigest() == gan_report.get("generated_sha256")):
            stored = pd.read_csv(paths.file("generated"))
            generated = stored[frame.columns]
            if len(generated) != gan_report["synthetic_rows"] or not np.isfinite(generated).all().all():
                raise ValueError("生成数据与审查记录不一致，请重新生成并确认")
            augmented = _fit(pd.concat([frame, generated], ignore_index=True), config.laptop.threads)
            aug_predictions, augmented_metrics = _evaluate(augmented, validation, names)
            report["validation"]["augmented"] = augmented_metrics
            if augmented_metrics["mae"] < real_metrics["mae"]:
                selected, selected_name = augmented, "wgan_augmented"
                validation_predictions = aug_predictions
            report["gan"]["validation_improved"] = augmented_metrics["mae"] < real_metrics["mae"]
            report["gan"]["used_for_final_model"] = selected_name == "wgan_augmented"
            stored["accepted_for_model"] = selected_name == "wgan_augmented"
            write_frame(paths.file("generated"), stored)
        else:
            raise ValueError("没有与当前训练集一致且通过检查的GAN数据，不能采用；请返回审查或仅用真实数据")
    else:
        report["gan"]["used_for_final_model"] = False
        report["gan"]["reason"] = "用户未采用GAN数据；仅使用真实训练集"
    # 测试标签直到验证选择完成后才用于评分。所有评估模型保留原始训练截止，不在测试中调参。
    predicted, metrics = _evaluate(selected, test, names)
    test_anchors = [s["baseline"] for s in test]
    naive = _scores([s["y"] for s in test], test_anchors, test_anchors)
    _, baseline_test = _evaluate(baseline, test, names)
    report.update(status="exploratory_model", selected_model=selected_name,
                  model={"name": "XGBoost", "device": "cpu", "n_estimators": 120, "max_depth": 2},
                  test={"selected": metrics, "naive": naive, "real_only": baseline_test,
                        "beats_naive": metrics["mae"] < naive["mae"]},
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
    comparison_plot({"Last observed": naive, "Real-only XGB": baseline_test,
                     "Selected XGB": metrics}, paths.file("comparison"))
    from xgboost import DMatrix

    test_features = pd.DataFrame([sample["x"] for sample in test], columns=names)
    contributions = selected.get_booster().predict(DMatrix(test_features), pred_contribs=True)
    shap_plot(names, np.abs(contributions[:, :-1]).mean(axis=0), paths.file("shap"))
    report["explanation"] = {"method": "XGBoost native TreeSHAP", "scope": "real_test_features_only",
                             "interpretation": "解释模型行为，不证明产业因果"}
    write_json(paths.file("result"), report)
    return report
