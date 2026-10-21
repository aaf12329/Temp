"""WGAN_Trainer 的显式适配层，允许组合原算法与本地质量、绘图功能。

结构：augment → 框架设置 → train_wgan_* → generate_samples → 质量检查/绘图。
调用：training 聚合入口 → augment；数据流：真实训练 DataFrame → WGAN-GP → 合成表和诊断。
调用方负责时间切分，本模块完全不知道验证/测试集；缺失、短样本与缺依赖明确回退。
"""

from importlib.metadata import PackageNotFoundError, version

import numpy as np

from app.common.file_io import write_frame, write_json
from app.common.gan_config import load_gan_settings
from app.pipeline.cleaning import generated_quality
from app.pipeline.plots import gan_plot, gan_tsne


def augment(frame, config, paths, threads: int):
    """仅拟合传入训练表，复用两后端原 WGAN-GP 算法；返回 (表或None, 报告)。"""
    config, source = load_gan_settings(config)
    requested = len(frame) * config.generation_multiplier
    report = {"model": "WGAN-GP", "backend": config.backend, "device": config.params.device,
              "params": config.params.model_dump(mode="json"), "status": "skipped",
              "real_train_rows": len(frame), "synthetic_rows": 0, "quality": None,
              "config_source": source, "generation_multiplier": config.generation_multiplier,
              "requested_synthetic_rows": requested, "user_accepted": False}
    if config.mode == "off" or len(frame) < config.min_real_samples:
        return None, {**report, "reason": f"未启用或真实训练样本不足 {config.min_real_samples} 行"}
    try:
        package = "torch" if config.backend == "pytorch" else "tensorflow-cpu"
        try:
            report["version"] = version(package)
        except PackageNotFoundError:
            if config.backend != "tensorflow":
                raise
            report["version"] = version("tensorflow")
        from wgan_tools.wgan_training import (
            WGANConfig,
            generate_samples,
            train_wgan_pytorch,
            train_wgan_tensorflow,
        )

        print(f"[WGAN配置] {source}", flush=True)
        print(f"[WGAN] {config.backend} {report['version']} / device={config.params.device} / "
              f"{len(frame)} 个真实训练样本 / {config.params.epochs} epochs", flush=True)
        trainer = train_wgan_pytorch if config.backend == "pytorch" else train_wgan_tensorflow
        params = WGANConfig(**config.params.model_dump())
        trained = trainer(frame, params, save_directory=paths.gan_directory(), overwrite=True)
        report["device"] = trained.device
        print(f"[生成数量] 真实训练 {len(frame)} × 5 = {requested}；原JSON固定数量不再生效", flush=True)
        generated = generate_samples(trained, requested, seed=params.seed,
                                      batch_size=config.generation_batch_size)
        # 变化量是原值的确定函数，不允许 GAN 自行生成互相矛盾的派生特征。
        if {"latest_value", "previous_value", "older_value", "change", "prior_change",
                "available_period_age"}.issubset(generated.columns):
            generated["change"] = generated["latest_value"] - generated["previous_value"]
            generated["prior_change"] = generated["previous_value"] - generated["older_value"]
            generated["available_period_age"] = np.rint(generated["available_period_age"]).clip(0, 2)
            valid = (generated[["latest_value", "previous_value", "older_value", "target"]]
                     .ge(0).all(axis=1))
            generated = generated.loc[valid].reset_index(drop=True)
        quality = generated_quality(frame, generated)
        marked = generated.copy()
        marked["is_synthetic"] = True
        marked["quality_passed"] = quality["accepted"]
        marked["accepted_for_model"] = False  # 初步检查通过仍须用户另行确认。
        write_frame(paths.file("generated"), marked)
        write_json(paths.file("quality"), quality)
        gan_plot(trained.history, paths.file("gan_plot"))
        tsne = gan_tsne(frame, generated, paths, accepted=quality["accepted"],
                        max_points=config.tsne_max_points, threads=None, seed=params.seed,
                        max_iter=config.tsne_max_iter, perplexity=config.tsne_perplexity)
        report.update(status="accepted" if quality["accepted"] else "rejected",
                      synthetic_rows=len(generated), quality=quality, reason=quality["reason"], tsne=tsne)
        print(f"[t-SNE] {tsne['status']}：真实训练 vs 生成样本，最多 {config.tsne_max_points} 点", flush=True)
        print(f"[WGAN] {report['status']}: {report['reason']}", flush=True)
        return generated if quality["accepted"] else None, report
    except (ImportError, PackageNotFoundError):
        hint = ("requirements/gan-pytorch.txt" if config.backend == "pytorch"
                else "requirements/gan-tensorflow.txt")
        return None, {**report, "status": "dependency_missing",
                      "reason": f"所选框架未安装；用根 .venv 安装 {hint}，本轮使用真实数据"}
    except (ValueError, RuntimeError, FloatingPointError) as exc:
        return None, {**report, "status": "failed",
                      "reason": f"GAN 训练或质量检查失败：{type(exc).__name__}；本轮使用真实数据"}
