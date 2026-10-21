"""CLI 真实本地管线验收，网络与付费模型使用假实现。

结构：输入/覆盖边界 → 无SQL完整流程 → 时间切分/增强选择 → CPU模型验收。
调用：pytest → CLI / 隔离桥接 → 本地算法；数据流：合成输入 → 临时产物与具体断言。
"""

import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.common.file_io import read_json, reset_pipeline_outputs, write_text
from app.common.paths import CRAWLER_SOURCE, WGAN_SOURCE, PipelinePaths
from app.contracts.pipeline import CLIConfig, GANParameters
from app.core.config import Settings
from app.core.errors import ServiceError
from app.pipeline.cleaning import clean_candidates, generated_quality, training_outliers
from app.pipeline_cli import choose_gan, read_urls, run
from tests.test_integration import mock_component_network as mock_component_network


@pytest.fixture
def legacy_imports(monkeypatch):
    """仅测试进程临时添加旧算法 import 路径；生产使用隔离子进程。"""
    monkeypatch.syspath_prepend(str(CRAWLER_SOURCE))
    monkeypatch.syspath_prepend(str(WGAN_SOURCE))
    yield
    for name in list(sys.modules):
        if name == "common" or name.startswith("common.") or name == "research" or name.startswith(
                "research.") or name == "wgan_tools" or name.startswith("wgan_tools."):
            sys.modules.pop(name, None)


def yearly_fixture(count=50):
    """合成同口径年度观测，发布时间为当年年末，绝非真实贵阳数据。"""
    return [{"series_id": "synthetic", "period": 1970 + i,
             "value": 100 + i * 2 + np.sin(i) * 5,
             "known_at": f"{1970 + i}-12-31", "scope": "SYNTHETIC ONLY"}
            for i in range(count)]


@pytest.fixture
def cli_settings(tmp_path):
    """无迁移的 CLI 设置；避免主服务 settings 夹具本身创建 SQL 数据库。"""
    return Settings(_env_file=None, api_token="test-cli-only-" + "a" * 32,
                    data_dir=tmp_path / "d", database_url="sqlite:///never-used.db",
                    advisor_api_key="")


def test_url_file_lines_duplicates_invalid_and_budget(tmp_path):
    """绝对地址、BOM、空行、注释、重复及超额网址均有明确行为。"""
    path = tmp_path / "网址 清单.txt"
    path.write_text("\ufeff# note\nhttps://example.test/a\n\nhttps://example.test/a\n"
                    "bad://invalid\nhttps://example.test/b\n", "utf-8")
    urls, skipped = read_urls('"' + str(path) + '"', 1)
    assert urls == ["https://example.test/a"]
    assert len(skipped) == 3
    assert "重复" in skipped[0]["reason"]
    assert "格式" in skipped[1]["reason"]
    assert "最多" in skipped[2]["reason"]


def test_config_cpu_budget_and_typo_rejected():
    """JSON 拼错及 CPU 档过大网络不静默接受。"""
    for payload in [{"epochs": 0}, {"device": "cuda:0"}, {"generator_layers": [4096]},
                    {"batchsize": 32}]:
        with pytest.raises(ValidationError):
            GANParameters.model_validate(payload)


@pytest.mark.parametrize("choice,expected", [("", "pytorch"), ("tf", "tensorflow"),
                                            ("2", "tensorflow"), ("pytorch", "pytorch")])
def test_terminal_framework_choice_preserves_json_parameters(choice, expected):
    """终端只选择框架，不覆盖 JSON 学习率等参数。"""
    config = CLIConfig()
    config.gan.params.learning_rate = 0.001
    choose_gan(config, lambda prompt: choice)
    assert config.gan.backend == expected
    assert config.gan.params.learning_rate == 0.001


def test_gan_skip_choice():
    """用户可以在终端跳过 GAN，继续真实数据建模。"""
    config = CLIConfig()
    choose_gan(config, lambda prompt: "0")
    assert config.gan.mode == "off"


def test_streamed_timeout_stops_interpreter_tree(tmp_path):
    """CLI 超时必须停止 Windows venv 启动器下真正的训练子进程。"""
    import psutil

    from app.common.component import _streamed_run

    marker = tmp_path / "child.txt"
    code = ("import subprocess,sys,time,pathlib; "
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
            f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)")
    with pytest.raises(subprocess.TimeoutExpired):
        _streamed_run([sys.executable, "-c", code], timeout=1.5, input="", text=True)
    assert marker.exists()
    assert not psutil.pid_exists(int(marker.read_text()))


def test_overwrite_registered_outputs_only(tmp_path):
    """重跑删除旧模型图表但保留用户自建文件，不递归删除目录。"""
    paths = PipelinePaths(tmp_path / "output")
    write_text(paths.file("report"), "old")
    write_text(paths.series("a" * 12).file("evaluation"), "old-image")
    extra = paths.root / "my-notes.txt"
    extra.write_text("keep", "utf-8")
    reset_pipeline_outputs(paths)
    assert not paths.file("report").exists()
    assert not paths.series("a" * 12).file("evaluation").exists()
    assert extra.read_text("utf-8") == "keep"


def test_overwrite_rejects_link_before_deleting(tmp_path):
    """目录联接/链接不可让覆盖操作越界；不支持建立链接的平台跳过。"""
    paths = PipelinePaths(tmp_path / "output")
    write_text(paths.file("report"), "must-remain")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        paths.series_parent().symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows user has no symlink privilege")
    with pytest.raises(ValueError):
        reset_pipeline_outputs(paths)
    assert paths.file("report").exists()


def test_rules_and_generated_quality_do_not_promote_facts():
    """规则保留 pending 状态并排除政策目标；复制原样本不能通过生成质量闸门。"""
    fact = {"fact_id": "f1", "value": 12, "claim_type": "observation", "subject": "s",
            "region": "r", "metric": "m", "unit": "元", "period": 2024,
            "known_at": "2025-01-01", "review_status": "pending", "uncertainty_flags": []}
    good, bad = clean_candidates([fact, {**fact, "claim_type": "target"}])
    assert len(good) == len(bad) == 1
    assert good[0]["review_status"] == fact["review_status"] == "pending"
    frame = pd.DataFrame(np.random.default_rng(42).normal(size=(100, 3)), columns=list("abc"))
    assert not generated_quality(frame, frame.copy())["accepted"]
    assert training_outliers(frame, CLIConfig().cleaning, 2)[1]["status"] == "evaluated"


def test_no_sql_cli_full_roundtrip(tmp_path, cli_settings, monkeypatch, mock_component_network):
    """假网络、真爬虫/提取/缺失诊断与演示助手，CSV 和来源输出且不创建数据库。"""
    from app import pipeline_cli

    settings = cli_settings
    settings.database_url = "sqlite:///never-used.db"
    paths = PipelinePaths(tmp_path / "o")
    monkeypatch.setattr(pipeline_cli, "CLI_LOCK", tmp_path / "cli.lock")
    listing = tmp_path / "urls.txt"
    listing.write_text("https://example.test/article\n", "utf-8")
    config = CLIConfig(retrieval="keywords", keywords=["酸汤"], subjects=["酸汤产业"],
                       regions=["凯里"], llm="demo")
    result = run(str(listing), "酸汤产业销售额", config, settings, paths=paths)
    assert result["status"] == "data_only"
    assert result["counts"]["candidate_facts"] >= 1
    assert result["llm_status"] == "completed"
    assert paths.file("information").read_bytes().startswith(b"\xef\xbb\xbf")
    assert "12亿元" in paths.file("information").read_text("utf-8-sig")
    assert read_json(paths.file("status"))["status"] == "completed"
    assert read_json(paths.file("access"))[0]["status"] == "ok"
    assert not list(tmp_path.rglob("*.db"))
    assert str(tmp_path) not in paths.file("analysis").read_text("utf-8")
    assert read_json(paths.file("analysis"))["facts"][0]["review_status"] == "pending"


def test_failed_crawl_still_exports_and_missing_key_is_explicit(tmp_path, cli_settings, monkeypatch):
    """全站被拒仍完成空表/原因/报告，空 key 不执行远程模型。"""
    from app import pipeline_cli

    class Denied:
        """合成拒绝适配器。"""

        def crawl_one(self, *args):
            """模拟 robots 拒绝，不发网络请求。"""
            raise ServiceError("E_NETWORK_POLICY", "robots.txt 禁止目标路径。", 403)

    settings = cli_settings
    monkeypatch.setattr(pipeline_cli, "CLI_LOCK", tmp_path / "cli.lock")
    listing = tmp_path / "urls.txt"
    listing.write_text("https://example.test/denied\n", "utf-8")
    paths = PipelinePaths(tmp_path / "o")
    result = run(str(listing), "销售额", CLIConfig(retrieval="keywords"), settings,
                 paths=paths, adapter=Denied())
    assert result["status"] == "data_only"
    assert result["llm_status"] == "missing_key"
    assert "robots.txt" in paths.file("access").read_text("utf-8")
    assert paths.file("cleaned").is_file()
    assert not list(tmp_path.rglob("*.db"))


def test_time_split_publication_and_real_model_charts(tmp_path, legacy_imports):
    """实际 CPU XGBoost + 三张真实性能图，训练标签时点早于验证起点，未触及未来发布。"""
    from app.pipeline.training import prepare_split, train_series

    config = CLIConfig()
    config.gan.mode = "off"
    rows = yearly_fixture()
    # 已知未来才公布的修订在当前分析时点被排除，不得进入历史训练。
    rows[20]["known_at"] = "2040-01-01"
    split, reasons, _ = prepare_split(rows, config, "2020-01-01")
    assert reasons and split is None
    rows = yearly_fixture()
    split, reasons, _ = prepare_split(rows, config, "2020-01-01")
    assert not reasons
    train, validation, test = split
    assert max(s["target_period"] for s in train) < min(s["target_period"] for s in validation)
    assert max(s["target_period"] for s in validation) < min(s["target_period"] for s in test)
    paths = PipelinePaths(tmp_path / "model")
    result = train_series(rows, config, paths, "2020-01-01")
    assert result["status"] == "exploratory_model"
    assert result["test"]["selected"]["n"] == 4
    assert paths.file("model").is_file()
    for name in ("evaluation", "importance", "trend"):
        assert paths.file(name).read_bytes().startswith(b"\x89PNG")


def test_rejected_gan_keeps_real_baseline_and_real_holdouts(tmp_path, legacy_imports, monkeypatch):
    """增强质量拒绝时用真实训练模型；GAN 从未看到验证/测试标签。"""
    from app.pipeline import training

    config = CLIConfig()
    config.gan.min_real_samples = 32
    rows = yearly_fixture()
    split, _, _ = training.prepare_split(rows, config, "2020-01-01")
    seen = []

    def reject(frame, gan, paths, threads):
        """替代耗时 GAN，只核对真实训练表边界并模拟质量拒绝。"""
        seen.append(frame.copy())
        assert len(frame) == len(split[0])
        np.testing.assert_allclose(frame["target"], [s["y"] for s in split[0]])
        return None, {"status": "rejected", "reason": "synthetic test: quality rejected"}

    monkeypatch.setattr(training, "augment", reject)
    result = training.train_series(rows, config, PipelinePaths(tmp_path / "m"), "2020-01-01")
    assert seen
    assert result["gan"]["status"] == "rejected"
    assert result["selected_model"] == "real_only"
    assert result["test"]["selected"]["n"] == config.test_size


@pytest.mark.parametrize("backend", ["pytorch", "tensorflow"])
def test_real_cpu_wgan_two_backends(tmp_path, legacy_imports, backend):
    """合成表实际梯度惩罚训练两轮，验证数值权重、生成标记与图表，不证明GAN质量。"""
    package = "torch" if backend == "pytorch" else "tensorflow"
    pytest.importorskip(package)
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    from app.pipeline.gan import augment

    config = CLIConfig()
    config.gan.backend = backend
    config.gan.params.epochs = 2
    config.gan.params.log_every = 1
    config.gan.params.generator_layers = (8, 8)
    config.gan.params.critic_layers = (8,)
    config.gan.params.n_critic = 1
    frame = pd.DataFrame(np.random.default_rng(42).normal(size=(64, 3)), columns=list("abc"))
    paths = PipelinePaths(tmp_path / backend)
    generated, report = augment(frame, config.gan, paths, 2)
    assert report["status"] in {"accepted", "rejected"}, report
    assert report["synthetic_rows"] == 64
    assert (paths.gan_directory() / "model.npz").is_file()
    assert paths.file("gan_plot").is_file()
    assert "is_synthetic" in paths.file("generated").read_text("utf-8-sig").splitlines()[0]
