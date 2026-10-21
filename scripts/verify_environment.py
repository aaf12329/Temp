"""统一环境显式验收入口，合成文本/数值，不访问真实研究数据。

结构：main → 本地 ONNX 推理/XGBoost CPU 拟合 → 相对路径环境报告。
调用：维护者或 Setup 验收阶段显式运行；数据流：合成输入 → 实际模型计算 → 可核对维度与版本。
模型准备须先完成；本脚本不联网或调用付费模型，导入本模块无文件副作用。
"""

import importlib.metadata
import json
import sys

from app.common.file_io import write_json
from app.common.paths import CRAWLER_SOURCE, ENVIRONMENT_CHECK, PROJECT_ROOT, DataPaths
from app.core.config import Settings


def main() -> None:
    """验证同一解释器中的关键运行能力并写报告；由显式脚本入口调用。"""
    import numpy as np
    import onnxruntime as ort
    from xgboost import XGBRegressor

    sys.path.insert(0, str(CRAWLER_SOURCE))
    from research.retrieval import Encoder

    encoder = Encoder(DataPaths(Settings().data_dir).model_directory())
    vectors = encoder.encode(["合成文本：酸汤产业的公开资料", "合成文本：餐饮统计口径"])
    assert vectors.shape == (2, 512) and np.isfinite(vectors).all()
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4)
    model = XGBRegressor(n_estimators=3, max_depth=2, n_jobs=1, tree_method="hist", device="cpu")
    model.fit(np.arange(40, dtype=np.float32).reshape(20, 2), np.arange(20, dtype=np.float32))
    predictions = model.predict(np.array([[2, 3]], dtype=np.float32))
    assert predictions.shape == (1,) and np.isfinite(predictions).all()
    packages = ["fastapi", "celery", "redis", "onnxruntime", "tokenizers", "scikit-learn",
                "industry-research-platform", "industry-research-advisor", "curl_cffi", "patchright"]
    result = {"python": sys.version.split()[0], "interpreter": ".venv/Scripts/python.exe"
              if sys.platform == "win32" else ".venv/bin/python",
              "packages": {name: importlib.metadata.version(name) for name in packages},
              "embedding_shape": list(vectors.shape), "onnx_provider": "CPUExecutionProvider",
              "available_onnx_providers": ort.get_available_providers(),
              "xgboost_cpu_fit": True, "paid_model_called": False,
              "report": ENVIRONMENT_CHECK.relative_to(PROJECT_ROOT).as_posix()}
    write_json(ENVIRONMENT_CHECK, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
