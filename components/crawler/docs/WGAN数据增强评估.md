# WGAN 数据增强评估（2026-10-20）

已克隆并检查 [WGAN_Trainer](https://github.com/aaf12329/WGAN_Trainer)，版本 `0475240eda77c50095d63918a30a2af1450bb4bf`。参考项目保持独立，未归并到 Crawler、Temp 或建议助手。

## 结论

WGAN-GP 的 CPU 训练与采样接口可用。本轮只在明确标注的合成夹具上验证功能和增强对照；**没有扩充真实产业数据**。当前研究资料尚无审核后可比序列，潜在精确观测也分散在三个口径各一个年度点，不能通过生成样本补成真实历史。

50 轮试验是功能验收，未证明收敛，也不证明 WGAN 在充分数据和合理调参下无效。当前设置对独立夹具的 XGBoost 结果变差，因此保持主研究流程不启用增强。

## 实际 CPU 试验

复用已有 CPU PyTorch 与可兼容的 Python 3.10 数据包；未安装 CUDA/新深度学习框架，未修改已有 Conda 环境。Torch 2.14.1+cpu，4 线程，设备 cpu。

| 项目 | 实测 |
|---|---|
| 数据来源 | 独立随机合成回归夹具；不是爬虫数据或真实行业样本 |
| 顺序切分 | 前 256 行训练，接着 64 行验证，最后 64 行测试 |
| WGAN 输入 | x1、x2、target 联合分布；只传训练部分 |
| 缩放器 | 仅训练部分拟合，断言 min/max 与训练表一致 |
| 网络 | G[32,32]、Critic[32,16]、latent=8、n_critic=2 |
| 训练 | 50 轮、batch=32、seed=42；训练加生成约 4.44 秒 |
| 采样 | 256 行，增量为原训练样本数的 1 倍，全部有限 |
| 无增强 XGBoost 验证 MAE | 0.2207 |
| 增强 XGBoost 验证 MAE | 1.3589 |
| 无增强 XGBoost 测试 MAE | 0.2770 |
| 增强 XGBoost 测试 MAE | 1.6182 |

未改动验证/测试表，未在它们上拟合填充器、缩放器或生成器。下游两个 XGBoost 使用相同固定参数，未凭测试结果搜索最优参数。生成表附 `is_synthetic=true` 和明确的数据来源；未写进 facts.csv 或 series.csv。

本次生成表 x1/target 相关系数从训练表的 0.943 变为 -0.373，显示短程设置尚未恢复关系。loss/GP 数值有限本身不能说明生成质量合格；不能用 t-SNE 看着相近代替真实留出指标。

## 已有接口与未来适配

参考仓库已提供真实 Python API：

```python
from wgan_tools.wgan_training import WGANConfig, train_wgan_pytorch, generate_samples

# 输入必须先完成真实数据切分；这里只是调用约定，不会替主研究自动生成数据。
result = train_wgan_pytorch(
    real_training_table,
    WGANConfig(device="cpu", gpu_memory_mb=0),
    save_directory=dedicated_model_directory,
)
synthetic_training_table = generate_samples(result, 200, seed=42)
```

以后由显式入口或隔离子进程适配；不把 WGAN 的通用 `common` 包与其他项目的同名包混在一个服务进程。原仓库默认 CLI 使用全表训练，且可能按缺失规则自动填充，**本项目不能直接把完整研究 CSV 扔给该 CLI 后声称无泄漏**。应调用训练 API，只传当前回测折的训练表。

未来适配器建议返回：

```text
{status, real_train_count, synthetic_count, schema,
 generator_repo_revision, generator_config, seed, train_cutoff,
 scaler_training_hash, parent_fact_ids, synthetic_rows, quality_metrics}
```

这个增强适配器合同目前是设计准备，尚未作为 research_api 的正式方法。正式接线需要：

1. 有足够且同口径的真实观测，测量时间与实际可得时间完整；按时间、主体/来源重复关系完成切分。
2. 先在训练折内清理、拟合预处理和生成器，任何测试/未来信息不进入训练。
3. 合成行附独立 ID、来源、父训练集指纹和权重，不能伪造 URL、证据、发布日期或 approved 状态。
4. 真实留出集保持不变；比较无增强、简单重采样和 WGAN 增强，多随机种子报告均值与波动。
5. 检查生成值约束、特征/目标关系、重复与记忆化；只有真实留出指标稳定改善才启用。

WGAN 可调整训练分布，不能增加独立真实证据或创造新的可信年度点。参考项目自身 README 也明确说明没有独立测试集质量评估；本轮额外的夹具对照不是产业效果验证。

依据：[参考项目](https://github.com/aaf12329/WGAN_Trainer)、[WGAN-GP 论文](https://arxiv.org/abs/1704.00028)。
