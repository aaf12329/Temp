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

原训练器的默认大网络和 5000 epochs 不作为轻薄本默认。平台使用 CPU、2 线程、100 epochs、latent_dim=16、G=[32,64]、D=[64,32]、batch=32、n_critic=2。生成量最多为真实训练集两倍，并受总量上限约束。保存数值 NPZ、JSON 和 CSV，不保存可执行 pickle。

样本量、Wasserstein 距离、多样性和近重复阈值均为工程启发式，不能证明数据真实或增强有效。数据不足不补事实标签、不补历史年份。未经来源和口径复核的输入仍是机器候选。

图表布局另参考 [WGAN_Boost_Xgboost](https://github.com/aaf12329/WGAN_Boost_Xgboost) 的 `ac2f6d3` 版本：真实值/预测值、残差、残差直方图和特征重要性；绘图代码由本项目实现，没有复制其中的随机划分、实验数据或模型权重。
