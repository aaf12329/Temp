"""路径地址簿（纯声明，无文件读写）。

结构：项目根 / 启动文件 / 环境 / 工作目录 / 外部工具名称。
调用：main 与测试读取常量；本模块不调用业务模块。
数据流：集中声明 → 调用方使用；不创建目录、不读写 CSV。
"""


from app.common.paths import OUTPUT_ROOT

# 平台适配：本组件不创建虚拟环境，只使用根 .venv；训练时始终传入输出目录。
RESULTS_DIRECTORY = OUTPUT_ROOT / "gan"
TRAINING_ARTIFACT_NAMES = {
    "model": "model.npz",
    "metadata": "model.json",
    "scaler": "normalizer.json",
    "history": "training_history.csv",
    "generated": "generated_data.csv",
    "encoded": "generated_encoded.csv",
    "processed": "training_data.csv",
    "processing": "processing_report.json",
    "loss_plot": "training_losses.png",
    "tsne": "tsne.png",
    "status": "run_status.json",
}
MODEL_ARTIFACT_NAMES = {name: TRAINING_ARTIFACT_NAMES[name]
                        for name in ("model", "metadata", "scaler", "history")}
