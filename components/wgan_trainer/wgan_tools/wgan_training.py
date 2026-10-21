"""WGAN-GP 训练与推理工具；两套后端在同一模块，框架按需导入。

结构树：WGANConfig / WGANResult；校验与设备；两后端建模与更新；保存/加载/生成。
调用树：train_wgan_pytorch / train_wgan_tensorflow → prepare_training → build_* → run_epochs
  run_epochs → 后端 step → gradient penalty；save_model → common.io。
  load_model → build_* / common.io；generate_samples → restore_categories。
数据流：有限数值表[N,F] → MinMax[-1,1] → G/Critic交替更新 → 权重+缩放器+schema
  → 分批采样[M,F] → 反归一化 → 独热投影/类别还原 → DataFrame。
契约：训练/加载返回 WGANResult；采样返回 DataFrame；保存返回产物路径字典。
边界：FP32；模型快照供推理，不含优化器续训状态；不导入数据处理/绘图功能模块。
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
import math
import sys
import time
from typing import Callable

import numpy as np
import pandas as pd
from pandas.api.types import is_complex_dtype, is_numeric_dtype
from sklearn.preprocessing import MinMaxScaler

from common.io import read_arrays, read_csv, read_json, write_arrays, write_csv, write_json
from common.artifacts import artifact_paths
from common.paths import MODEL_ARTIFACT_NAMES, TRAINING_ARTIFACT_NAMES


@dataclass(frozen=True)
class WGANConfig:
    """两个后端共用参数；显存预算单位 MiB，仅 CUDA 生效。"""

    epochs: int = 5000
    batch_size: int = 64
    latent_dim: int = 100
    n_critic: int = 5
    learning_rate: float = 0.0002
    lambda_gp: float = 10.0
    beta1: float = 0.5
    beta2: float = 0.9
    generator_layers: tuple[int, ...] = (128, 256, 512)
    critic_layers: tuple[int, ...] = (256, 128, 64)
    device: str = "auto"
    gpu_memory_mb: int = 8192
    seed: int = 42
    log_every: int = 1


@dataclass
class WGANResult:
    """内存中的训练结果；history 的 D_loss 已包含 lambda_gp × GP。"""

    backend: str
    generator: object
    critic: object
    scaler: MinMaxScaler
    columns: list[str]
    category_schema: dict
    config: WGANConfig
    device: str
    history: pd.DataFrame = field(default_factory=pd.DataFrame)
    artifacts: dict = field(default_factory=dict)


def validate_config(config: WGANConfig) -> None:
    """在分配模型/显存之前拒绝不合法的训练参数。"""
    for name in ("epochs", "batch_size", "latent_dim", "n_critic", "log_every"):
        value = getattr(config, name)
        if type(value) is not int or value < (2 if name == "batch_size" else 1):
            raise ValueError(f"{name} 必须为正整数，batch_size 至少为 2。")
    if type(config.seed) is not int or not 0 <= config.seed <= 2**32 - 1:
        raise ValueError("seed 必须为 0 到 4294967295 的整数。")
    if type(config.gpu_memory_mb) is not int or config.gpu_memory_mb < 0:
        raise ValueError("gpu_memory_mb 必须为非负整数。")
    for name in ("generator_layers", "critic_layers"):
        layers = getattr(config, name)
        if not layers or len(layers) > 8 or any(type(n) is not int or n < 1 for n in layers):
            raise ValueError(f"{name} 必须包含 1 至 8 个正整数。")
    for name in ("learning_rate", "lambda_gp", "beta1", "beta2"):
        value = getattr(config, name)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{name} 必须是有限数值。")
    if config.learning_rate <= 0 or config.lambda_gp < 0:
        raise ValueError("学习率必须大于 0，梯度惩罚权重不能小于 0。")
    if not 0 <= config.beta1 < 1 or not 0 <= config.beta2 < 1:
        raise ValueError("Adam beta 必须在 [0,1) 范围内。")
    if config.device not in ("cpu", "auto") and not (
        config.device.startswith("cuda:") and config.device[5:].isdigit()
    ):
        raise ValueError("device 应为 auto、cpu 或 cuda:0 等设备。")


def numeric_table(data: pd.DataFrame, *, min_rows: int = 1) -> np.ndarray:
    """验证数值表，不静默转换文本/复数或接受 NaN。"""
    if not isinstance(data, pd.DataFrame) or len(data) < min_rows or data.shape[1] < 1:
        raise ValueError(f"需要至少 {min_rows} 行、1 列的 DataFrame。")
    if data.columns.has_duplicates or any(not isinstance(c, str) or not c for c in data.columns):
        raise ValueError("字段名必须为非空、唯一字符串。")
    if any(not is_numeric_dtype(t) or is_complex_dtype(t) for t in data.dtypes):
        raise ValueError("训练输入必须为实数数值字段；先处理缺失和类别编码。")
    array = data.to_numpy(dtype=np.float64)
    if not np.isfinite(array).all():
        raise ValueError("数据含 NaN/无穷值；请先完成数据处理。")
    return array


def validate_schema(data: pd.DataFrame, schema: dict) -> None:
    """确保独热字段确实对应互斥类别，防止错误还原。"""
    used = set()
    for column, categories in schema.get("categories", {}).items():
        if not categories or len(set(categories)) != len(categories):
            raise ValueError(f"{column} 类别映射为空或重复。")
        names = [f"{column}_{category}" for category in categories]
        if used.intersection(names) or any(name not in data for name in names) or column in data:
            raise ValueError(f"{column} 的独热映射与训练字段不一致。")
        values = data[names].to_numpy()
        if not np.isin(values, (0, 1)).all() or not (values.sum(axis=1) == 1).all():
            raise ValueError(f"{column} 必须为完整、互斥的独热编码。")
        used.update(names)


def prepare_training(data, config, category_schema):
    """共用归一化准备；缩放器只拟合传入的训练表。"""
    validate_config(config)
    array = numeric_table(data, min_rows=2)
    schema = category_schema if category_schema is not None else data.attrs.get(
        "processing_report", {}).get("encoding", {})
    validate_schema(data, schema)
    scaler = MinMaxScaler(feature_range=(-1, 1))
    normalized = scaler.fit_transform(array).astype(np.float32)
    if not np.isfinite(normalized).all():
        raise ValueError("归一化后出现非有限值，请检查极端数值。")
    return normalized, scaler, schema


def torch_device(torch, config):
    """选定 Torch 设备并在建模前限制当前进程的 CUDA 分配器预算。"""
    device = config.device
    if device == "auto":
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
    if device != "cpu":
        index = int(device.split(":")[1])
        if not torch.cuda.is_available() or index >= torch.cuda.device_count():
            raise RuntimeError("PyTorch CUDA 不可用，请安装 CUDA 版 torch 或选择 CPU。")
        total = torch.cuda.get_device_properties(index).total_memory
        if not 0 < config.gpu_memory_mb * 1024**2 <= total:
            raise ValueError("CUDA 显存预算必须大于 0，且不能超过物理显存。")
        torch.cuda.set_per_process_memory_fraction(config.gpu_memory_mb * 1024**2 / total, index)
    return device


def build_torch(torch, config, features, device):
    """建立与参考工程一致的 Dense/BN/LeakyReLU 生成器和线性 Critic。"""
    nn = torch.nn

    def network(widths, input_size, generator):
        """构建PyTorch顺序网络；由build_torch分别建立G/Critic。"""
        layers = []
        for width in widths:
            layers.extend([nn.Linear(input_size, width), nn.ReLU()])
            if generator:
                layers.append(nn.BatchNorm1d(width, eps=0.001, momentum=0.01))
            layers.append(nn.LeakyReLU(0.2))
            input_size = width
        layers.append(nn.Linear(input_size, features if generator else 1))
        if generator:
            layers.append(nn.Tanh())
        model = nn.Sequential(*layers).to(device)
        for layer in model:
            if isinstance(layer, nn.Linear):
                nn.init.xavier_uniform_(layer.weight)
                nn.init.zeros_(layer.bias)
        return model

    return (network(config.generator_layers, config.latent_dim, True),
            network(config.critic_layers, features, False))


def tensorflow_device(tf, config):
    """配置 TF 设备；当前 Windows 原生 TF2.11+ 强制采用 CPU。"""
    if sys.platform == "win32":
        if config.device not in ("auto", "cpu"):
            raise RuntimeError("Windows 原生 TensorFlow 2.11+ 不支持 CUDA，请选择 CPU 或 PyTorch。")
        return "/CPU:0"
    gpus = tf.config.list_physical_devices("GPU")
    if config.device == "cpu" or (config.device == "auto" and not gpus):
        return "/CPU:0"
    index = 0 if config.device == "auto" else int(config.device.split(":")[1])
    if index >= len(gpus) or config.gpu_memory_mb <= 0:
        raise RuntimeError("TensorFlow GPU 不可用或显存预算无效，请选择 CPU。")
    try:
        tf.config.set_logical_device_configuration(gpus[index], [
            tf.config.LogicalDeviceConfiguration(memory_limit=config.gpu_memory_mb)])
    except RuntimeError as error:
        raise RuntimeError("TensorFlow GPU 已初始化，请重启进程后再设置显存预算。") from error
    return f"/GPU:{index}"


def build_tensorflow(tf, config, features):
    """构造 FP32 Keras 网络；训练时不冻结 Critic.trainable。"""
    layers = tf.keras.layers

    def network(widths, input_size, generator):
        """构建TensorFlow顺序网络；由build_tensorflow分别建立G/Critic。"""
        sequence = [layers.Input(shape=(input_size,), dtype="float32")]
        for width in widths:
            sequence.append(layers.Dense(width, activation="relu", dtype="float32"))
            if generator:
                sequence.append(layers.BatchNormalization(dtype="float32"))
            sequence.append(layers.LeakyReLU(negative_slope=0.2, dtype="float32"))
        sequence.append(layers.Dense(features if generator else 1,
                                     activation="tanh" if generator else None, dtype="float32"))
        return tf.keras.Sequential(sequence)

    return (network(config.generator_layers, config.latent_dim, True),
            network(config.critic_layers, features, False))


def run_epochs(result, normalized, step, logger, save_directory):
    """共用批次调度和按样本数加权的 epoch 指标；单行尾批并入前一批。"""
    config = result.config
    rng = np.random.default_rng(config.seed)
    history = []
    history_path = artifact_paths(save_directory, MODEL_ARTIFACT_NAMES)["history"] if save_directory is not None else None
    for epoch in range(1, config.epochs + 1):
        started = time.perf_counter()
        batches = list(np.array_split(rng.permutation(len(normalized)),
                                    max(1, math.ceil(len(normalized) / config.batch_size))))
        # array_split 均匀分配；N=3,batch=2 时仍可能留下 singleton。
        if len(batches) > 1 and len(batches[-1]) == 1:
            batches[-2] = np.concatenate([batches[-2], batches[-1]])
            batches.pop()
        totals = np.zeros(4)
        for indices in batches:
            metrics = np.asarray(step(normalized[indices]), dtype=float)
            if metrics.shape != (4,) or not np.isfinite(metrics).all():
                raise FloatingPointError(f"第 {epoch} 轮出现非有限损失，请降低学习率并检查输入。")
            totals += metrics * len(indices)
        d_loss, g_loss, gp, wasserstein = totals / len(normalized)
        history.append({"epoch": epoch, "d_loss": d_loss, "g_loss": g_loss,
                        "gp": gp, "wasserstein": wasserstein,
                        "seconds": time.perf_counter() - started})
        result.history = pd.DataFrame(history)
        if save_directory is not None:
            write_csv(history_path, result.history)
        if logger and (epoch == 1 or epoch % config.log_every == 0 or epoch == config.epochs):
            logger(f"Epoch {epoch}/{config.epochs} | D_loss={d_loss:.6f} | G_loss={g_loss:.6f}"
                   f" | GP={gp:.6f} | {history[-1]['seconds']:.2f}s | {result.device}")
    if save_directory is not None:
        result.artifacts = save_model(result, save_directory, overwrite=True)
    return result


def check_save_directory(directory, overwrite):
    """工具调用默认保护已有模型；CLI 按用户要求显式允许覆盖。"""
    if directory is not None and not overwrite and any(
        path.exists() for path in artifact_paths(directory, MODEL_ARTIFACT_NAMES).values()
    ):
        raise FileExistsError("已有训练产物；如需覆盖请设置 overwrite=True。")


def train_wgan_pytorch(data: pd.DataFrame, config: WGANConfig | None = None, *,
                       save_directory=None, category_schema=None, logger: Callable | None = print,
                       overwrite=False) -> WGANResult:
    """训练 PyTorch WGAN-GP；Critic 每批更新 n_critic 次，G 更新一次。"""
    config = config or WGANConfig()
    check_save_directory(save_directory, overwrite)
    normalized, scaler, schema = prepare_training(data, config, category_schema)
    import torch

    torch.manual_seed(config.seed)
    device = torch_device(torch, config)
    generator, critic = build_torch(torch, config, data.shape[1], device)
    optimizer_g = torch.optim.Adam(generator.parameters(), lr=config.learning_rate,
                                   betas=(config.beta1, config.beta2), eps=1e-7)
    optimizer_d = torch.optim.Adam(critic.parameters(), lr=config.learning_rate,
                                   betas=(config.beta1, config.beta2), eps=1e-7)
    result = WGANResult("pytorch", generator, critic, scaler, list(data.columns), schema, config, device)

    def step(batch):
        """完成一批PyTorch的Critic/G更新；由run_epochs调用。"""
        real = torch.as_tensor(batch, device=device)
        size = len(batch)
        metrics = np.zeros(3)
        generator.train()
        critic.train()
        for _ in range(config.n_critic):
            optimizer_d.zero_grad(set_to_none=True)
            with torch.no_grad():
                fake = generator(torch.randn(size, config.latent_dim, device=device))
            alpha = torch.rand(size, 1, device=device)
            interpolated = (alpha * real + (1 - alpha) * fake).requires_grad_(True)
            scores = critic(interpolated)
            gradients = torch.autograd.grad(scores, interpolated, torch.ones_like(scores),
                                            create_graph=True)[0]
            gp = ((torch.sqrt(torch.sum(gradients**2, dim=1) + 1e-12) - 1)**2).mean()
            wasserstein = critic(fake).mean() - critic(real).mean()
            d_loss = wasserstein + config.lambda_gp * gp
            d_loss.backward()
            optimizer_d.step()
            metrics += [d_loss.item(), gp.item(), wasserstein.item()]
        optimizer_g.zero_grad(set_to_none=True)
        for parameter in critic.parameters():
            parameter.requires_grad_(False)
        try:
            g_loss = -critic(generator(torch.randn(size, config.latent_dim, device=device))).mean()
            g_loss.backward()
            optimizer_g.step()
        finally:
            for parameter in critic.parameters():
                parameter.requires_grad_(True)
        metrics /= config.n_critic
        return metrics[0], g_loss.item(), metrics[1], metrics[2]

    return run_epochs(result, normalized, step, logger, save_directory)


def train_wgan_tensorflow(data: pd.DataFrame, config: WGANConfig | None = None, *,
                          save_directory=None, category_schema=None, logger: Callable | None = print,
                          overwrite=False) -> WGANResult:
    """训练 TensorFlow WGAN-GP；嵌套 GradientTape 对输入梯度惩罚求参数梯度。"""
    config = config or WGANConfig()
    check_save_directory(save_directory, overwrite)
    normalized, scaler, schema = prepare_training(data, config, category_schema)
    import tensorflow as tf

    tf.keras.utils.set_random_seed(config.seed)
    device = tensorflow_device(tf, config)
    with tf.device(device):
        generator, critic = build_tensorflow(tf, config, data.shape[1])
        optimizer_g = tf.keras.optimizers.Adam(config.learning_rate, beta_1=config.beta1, beta_2=config.beta2)
        optimizer_d = tf.keras.optimizers.Adam(config.learning_rate, beta_1=config.beta1, beta_2=config.beta2)
    result = WGANResult("tensorflow", generator, critic, scaler, list(data.columns), schema, config, device)

    @tf.function(reduce_retracing=True)
    def critic_step(real):
        """计算嵌套输入梯度惩罚并更新Critic；由TensorFlow批次step调用。"""
        size = tf.shape(real)[0]
        fake = tf.stop_gradient(generator(tf.random.normal((size, config.latent_dim)), training=True))
        with tf.GradientTape() as tape:
            alpha = tf.random.uniform((size, 1))
            interpolated = alpha * real + (1 - alpha) * fake
            with tf.GradientTape() as input_tape:
                input_tape.watch(interpolated)
                interpolated_scores = critic(interpolated, training=True)
            gradients = input_tape.gradient(interpolated_scores, interpolated)
            gp = tf.reduce_mean((tf.sqrt(tf.reduce_sum(gradients**2, axis=1) + 1e-12) - 1)**2)
            wasserstein = tf.reduce_mean(critic(fake, training=True)) - tf.reduce_mean(critic(real, training=True))
            d_loss = wasserstein + config.lambda_gp * gp
        optimizer_d.apply_gradients(zip(tape.gradient(d_loss, critic.trainable_variables), critic.trainable_variables))
        return d_loss, gp, wasserstein

    @tf.function(reduce_retracing=True)
    def generator_step(size):
        """对生成器目标求梯度并更新；由TensorFlow批次step调用。"""
        with tf.GradientTape() as tape:
            fake = generator(tf.random.normal((size, config.latent_dim)), training=True)
            g_loss = -tf.reduce_mean(critic(fake, training=True))
        optimizer_g.apply_gradients(zip(tape.gradient(g_loss, generator.trainable_variables), generator.trainable_variables))
        return g_loss

    def step(batch):
        """汇总TensorFlow一批的Critic/G指标；由run_epochs调用。"""
        with tf.device(device):
            real = tf.convert_to_tensor(batch)
            metrics = np.zeros(3)
            for _ in range(config.n_critic):
                metrics += [float(value.numpy()) for value in critic_step(real)]
            g_loss = float(generator_step(tf.constant(len(batch))).numpy())
            metrics /= config.n_critic
            return metrics[0], g_loss, metrics[1], metrics[2]

    return run_epochs(result, normalized, step, logger, save_directory)


def scaler_metadata(result):
    """以可读 JSON 保存 MinMaxScaler 所有逆变换状态。"""
    scaler = result.scaler
    return {"class": "MinMaxScaler", "feature_range": [-1, 1], "columns": result.columns,
            "n_samples_seen": int(scaler.n_samples_seen_),
            **{name: getattr(scaler, name).tolist() for name in
               ("min_", "scale_", "data_min_", "data_max_", "data_range_")}}


def save_model(result: WGANResult, directory, *, overwrite=False) -> dict:
    """保存 G/Critic 数值权重、独立标准化器、类别映射和历史；覆盖须显式启用。"""
    paths = artifact_paths(directory, MODEL_ARTIFACT_NAMES)
    if not overwrite and any(path.exists() for path in paths.values()):
        raise FileExistsError("结果文件已存在；调用方应显式选择 overwrite=True。")
    arrays = {}
    for prefix, model in (("g", result.generator), ("d", result.critic)):
        if result.backend == "pytorch":
            arrays.update({f"{prefix}:{key}": value.detach().cpu().numpy()
                           for key, value in model.state_dict().items()})
        elif result.backend == "tensorflow":
            arrays.update({f"{prefix}:{i}": value for i, value in enumerate(model.get_weights())})
        else:
            raise ValueError("未知训练后端。")
    write_arrays(paths["model"], arrays)
    write_json(paths["scaler"], scaler_metadata(result))
    write_csv(paths["history"], result.history)
    write_json(paths["metadata"], {"format_version": 1, "backend": result.backend,
        "config": asdict(result.config), "columns": result.columns,
        "category_schema": result.category_schema, "training_device": result.device,
        "purpose": "inference_snapshot_without_optimizer_state"})
    return {name: str(path.resolve()) for name, path in paths.items()}


def load_model(directory, *, device="cpu") -> WGANResult:
    """在指定设备重建模型和标准化器，返回统一推理结果；不执行续训。"""
    status_path = artifact_paths(directory, TRAINING_ARTIFACT_NAMES)["status"]
    if status_path.exists() and read_json(status_path).get("status") != "complete":
        raise ValueError("该目录最近一次流程未完成，请检查 run_status 后重新运行，避免混用不同轮次产物。")
    paths = artifact_paths(directory, MODEL_ARTIFACT_NAMES)
    metadata, state = read_json(paths["metadata"]), read_json(paths["scaler"])
    if metadata.get("format_version") != 1 or state.get("columns") != metadata.get("columns"):
        raise ValueError("模型格式版本或标准化器字段不一致。")
    saved_config = {**metadata["config"], "device": device}
    for name in ("generator_layers", "critic_layers"):
        saved_config[name] = tuple(saved_config[name])
    config = WGANConfig(**saved_config)
    validate_config(config)
    columns = metadata["columns"]
    scaler = MinMaxScaler(feature_range=(-1, 1))
    for name in ("min_", "scale_", "data_min_", "data_max_", "data_range_"):
        value = np.asarray(state[name], dtype=np.float64)
        if value.shape != (len(columns),) or not np.isfinite(value).all():
            raise ValueError("标准化器的数值状态损坏。")
        setattr(scaler, name, value)
    if np.any(scaler.scale_ <= 0):
        raise ValueError("标准化器 scale_ 必须为正。")
    scaler.n_features_in_ = len(columns)
    scaler.n_samples_seen_ = state["n_samples_seen"]
    arrays = read_arrays(paths["model"])
    backend = metadata["backend"]
    if backend == "pytorch":
        import torch
        actual_device = torch_device(torch, config)
        generator, critic = build_torch(torch, config, len(columns), actual_device)
        for prefix, model in (("g", generator), ("d", critic)):
            model.load_state_dict({key: torch.as_tensor(arrays[f"{prefix}:{key}"], device=actual_device)
                                   for key in model.state_dict()}, strict=True)
            model.eval()
    elif backend == "tensorflow":
        import tensorflow as tf
        actual_device = tensorflow_device(tf, config)
        with tf.device(actual_device):
            generator, critic = build_tensorflow(tf, config, len(columns))
            for prefix, model in (("g", generator), ("d", critic)):
                model.set_weights([arrays[f"{prefix}:{i}"] for i in range(len(model.get_weights()))])
    else:
        raise ValueError("未知模型后端。")
    return WGANResult(backend, generator, critic, scaler, columns, metadata["category_schema"], config,
                      actual_device, read_csv(paths["history"]).apply(pd.to_numeric), {k: str(v) for k, v in paths.items()})


def project_categories(data: pd.DataFrame, schema: dict) -> pd.DataFrame:
    """将连续生成的独热组投影为唯一类别；保持编码表供后续分析。"""
    result = data.copy()
    for column, categories in schema.get("categories", {}).items():
        names = [f"{column}_{category}" for category in categories]
        winners = np.argmax(result[names].to_numpy(), axis=1)
        result[names] = np.eye(len(names))[winners]
    return result


def restore_categories(data: pd.DataFrame, schema: dict) -> pd.DataFrame:
    """按保存映射还原类别文本和原字段顺序，不推测字符串前缀。"""
    result = project_categories(data, schema)
    for column, categories in schema.get("categories", {}).items():
        names = [f"{column}_{category}" for category in categories]
        result[column] = np.asarray(categories)[np.argmax(result[names].to_numpy(), axis=1)]
        result = result.drop(columns=names)
    order = schema.get("input_columns", [])
    if order:
        if set(order) != set(result.columns):
            raise ValueError("类别还原后的字段与保存映射不一致。")
        result = result[order]
    return result


def generate_samples(result: WGANResult, n_samples: int, *, batch_size=256, seed=None,
                     decode_categories=True, output_path=None) -> pd.DataFrame:
    """分批生成并反归一化；独立 NumPy RNG，不重置训练后端的随机种子。"""
    if type(n_samples) is not int or n_samples < 1 or type(batch_size) is not int or batch_size < 1:
        raise ValueError("生成行数和批大小必须为正整数。")
    rng = np.random.default_rng(result.config.seed if seed is None else seed)
    generated = []
    previous_training = None
    if result.backend == "pytorch":
        import torch
        previous_training = result.generator.training
        result.generator.eval()
    try:
        for start in range(0, n_samples, batch_size):
            noise = rng.standard_normal((min(batch_size, n_samples - start), result.config.latent_dim)).astype(np.float32)
            if result.backend == "pytorch":
                with torch.no_grad():
                    values = result.generator(torch.as_tensor(noise, device=result.device)).cpu().numpy()
            elif result.backend == "tensorflow":
                import tensorflow as tf
                with tf.device(result.device):
                    values = result.generator(noise, training=False).numpy()
            else:
                raise ValueError("未知模型后端。")
            inverse = result.scaler.inverse_transform(values.astype(np.float64))
            constant = result.scaler.data_range_ == 0
            inverse[:, constant] = result.scaler.data_min_[constant]
            generated.append(inverse)
    finally:
        if previous_training is not None:
            result.generator.train(previous_training)
    frame = pd.DataFrame(np.concatenate(generated), columns=result.columns)
    numeric_table(frame)
    frame = project_categories(frame, result.category_schema)
    if decode_categories:
        frame = restore_categories(frame, result.category_schema)
    if output_path is not None:
        write_csv(Path(output_path), frame)
    return frame


if __name__ == "__main__":
    validate_config(WGANConfig(epochs=1))
    print("WGAN-GP configuration self-check passed; no framework imported or training started.")
