"""原仓库 GAN JSON 适配，共用配置设施，无框架或网络副作用。

结构：load_gan_settings；调用：CLI/workflow → 此处 → 地址簿/JSON契约。
数据流：training/preprocessing/charts JSON → 原样训练参数与图形参数 → GAN配置。
只映射实际采用的训练/绘图字段，不把原实验的 Reference/Pollutant 列套到产业表。
"""

import hashlib

from app.common.file_io import read_bytes, read_json
from app.common.paths import project_input
from app.contracts.pipeline import GANParameters


def load_gan_settings(config):
    """读取指定原结构JSON并保留所有训练与图形参数；不做性能降档。"""
    if config.config_file is None:
        return config, {"source": "explicit_inline", "training": config.params.model_dump(mode="json")}
    path = project_input(config.config_file)
    source = read_json(path)
    if set(source) != {"training", "preprocessing", "charts"}:
        raise ValueError("WGAN JSON 需要 training、preprocessing、charts 三部分。")
    training, charts = source["training"], source["charts"]
    keys = set(GANParameters.model_fields)
    if set(training) != keys | {"n_generate", "generation_batch_size", "precision"}:
        raise ValueError("training 字段需与 WGAN_Trainer 原配置一致，请检查缺项或拼写。")
    if set(charts) != {"style", "tsne_perplexity", "tsne_max_samples_per_group", "tsne_max_iter"}:
        raise ValueError("charts 字段需与 WGAN_Trainer 原配置一致。")
    if charts["style"] != "standard":
        raise ValueError("当前原仓库绘图仅支持 standard 风格。")
    resolved = config.model_copy(deep=True)
    resolved.params = GANParameters.model_validate({key: training[key] for key in keys})
    resolved.n_generate = training["n_generate"]
    resolved.generation_batch_size = training["generation_batch_size"]
    resolved.precision = training["precision"]
    resolved.tsne_max_points = charts["tsne_max_samples_per_group"] * 2
    resolved.tsne_max_iter = charts["tsne_max_iter"]
    resolved.tsne_perplexity = charts["tsne_perplexity"]
    resolved = type(config).model_validate(resolved.model_dump())
    return resolved, {"source": config.config_file,
                      "sha256": hashlib.sha256(read_bytes(path)).hexdigest(),
                      "training": training, "charts": charts,
                      "preprocessing_scope": "年度建模输入已经完整数值化，不套用原实验专用列名或补标签"}
