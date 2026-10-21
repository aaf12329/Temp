# WGAN 算法来源与平台适配

来源：[aaf12329/WGAN_Trainer](https://github.com/aaf12329/WGAN_Trainer)，选取版本 `0475240`。完整源码版本以本目录 `source.json` 为准。原始独立仓库未修改。

本平台只选用 `wgan_tools/wgan_training.py` 的 TensorFlow/PyTorch WGAN-GP 与 `missing_statistics.py` 的缺失诊断；不复制原仓库环境、训练数据、权重、结果或 CUDA 配置。`common/artifacts.py` 保留纯路径映射；common/paths 改为平台输出地址簿，common/io 委托平台原子 I/O。训练算法两文件保留来源内容，不新增本地 LLM。

调用与数据结构：

```text
CLI → 隔离 bridge → pipeline/workflow
  ├─ missing_statistics.analyze_missing_statistics(DataFrame[N,7]) → dict
  └─ pipeline/training：真实时间切分
       └─ pipeline/gan.augment(真实训练 DataFrame[N,7])
            ├─ WGANConfig ← config/cli.json 的 gan.params
            ├─ train_wgan_pytorch / train_wgan_tensorflow
            ├─ generate_samples → DataFrame[M,7]
            └─ 训练分布质量闸门 → 真实验证集比较 → 选中模型 → 真实测试集
```

所有者后续要求GAN不考虑性能兼容，直接使用原JSON。config/wgan.json仍是源仓库config.json的逐字节副本：5000 epochs、原网络、每组t-SNE最多1000点且1000次迭代。随后所有者明确修改平台生成策略：原training.n_generate=2000仅保留来源兼容，实际generate_samples数量改为真实训练表行数×5，不含验证/测试。训练前用户选择是否尝试，生成后显示t-SNE与数值检查，再确认是否采用；建模阶段读取同一批带哈希的产物，不重新生成。原实验专用列名不套到产业年度表；实际数量、配置来源哈希与用户决策均记录。只使用根.venv，不创建原仓库环境。

样本量、Wasserstein 距离、多样性和近重复阈值均为工程启发式，不能证明数据真实或增强有效。数据不足不补事实标签、不补历史年份。未经来源和口径复核的输入仍是机器候选。

图表布局另参考 [WGAN_Boost_Xgboost](https://github.com/aaf12329/WGAN_Boost_Xgboost) 的 `ac2f6d3` 版本：真实值/预测值、残差、残差直方图和特征重要性；绘图代码由本项目实现，没有复制其中的随机划分、实验数据或模型权重。
