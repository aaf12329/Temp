"""独立 CPU 模型冒烟与资源观测，不联网、不读真实研究数据、不调用付费模型。

结构：main → 单后端默认档训练 → 编码/质量/图表 → 资源记录。
调用：维护者每次用新进程运行一个 backend；数据流：合成64x3表 → 实际模型 → work/cli-check。
运行耗时和内存属于执行机器的观测，不能冒充用户 i5 笔记本的性能实测。
"""

import argparse
import sys
import time

import numpy as np
import pandas as pd
import psutil

from app.common.file_io import write_json
from app.common.paths import CLI_CHECK_ROOT, CRAWLER_SOURCE, WGAN_SOURCE, PipelinePaths
from app.contracts.pipeline import CLIConfig


def main():
    """用根环境单后端 CPU 实际训练默认100轮并记录峰值工作集。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["pytorch", "tensorflow"], required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(WGAN_SOURCE))
    sys.path.insert(0, str(CRAWLER_SOURCE))
    from app.pipeline.gan import augment

    config = CLIConfig()
    config.gan.backend = args.backend
    paths = PipelinePaths(CLI_CHECK_ROOT / args.backend)
    frame = pd.DataFrame(np.random.default_rng(42).normal(size=(64, 3)), columns=list("abc"))
    started = time.perf_counter()
    generated, report = augment(frame, config.gan, paths, config.laptop.threads)
    memory = psutil.Process().memory_info()
    result = {"backend": args.backend, "version": report.get("version"), "device": "cpu",
              "epochs": config.gan.params.epochs, "shape": list(frame.shape),
              "seconds": round(time.perf_counter() - started, 3),
              "peak_rss_mb": round(getattr(memory, "peak_wset", memory.rss) / 1024**2, 2),
              "status": report["status"], "synthetic_rows": report["synthetic_rows"],
              "quality_accepted": generated is not None, "synthetic_fixture_only": True}
    write_json(paths.file("runtime"), result)
    assert report["status"] in {"accepted", "rejected"}, report
    assert paths.file("generated").exists()
    print(result)


if __name__ == "__main__":
    main()
