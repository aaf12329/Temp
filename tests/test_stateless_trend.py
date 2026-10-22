"""单次趋势分析隔离用例，不联网、不调用真实模型。

结构：独立请求 → 禁止会话I/O → 最终输出；调用：pytest → 正式适配器。
数据流：合成证据 → 假模型 → 趋势回复和本轮结果存档。
"""
from advisor_bot.common import ModelReply

from app.advisor.integrated import IntegratedAdvisor
from app.common.file_io import read_json
from app.common.paths import PipelinePaths
from app.contracts.pipeline import CLIConfig
from app.core.config import Settings
from app.pipeline_cli import summarize


def test_independent_trend_requests_never_use_memory(tmp_path, monkeypatch):
    """同一对象连续分析只带本轮内容，不创建会话目录。"""
    calls, configurations = [], []

    class Model:
        """记录本轮输入的模型替身。"""

        def complete(self, messages):
            """记录消息并返回固定趋势结论；供正式适配器调用。"""
            calls.append(messages)
            return ModelReply(content="当前数据不足，无法判断发展趋势。")

    def factory(config):
        """记录无会话的模型配置；由适配器调用。"""
        configurations.append(config)
        return Model()

    def forbidden(*args, **kwargs):
        """会话或历史入口一旦被调用就使本用例失败。"""
        raise AssertionError("不得访问会话/记忆")

    settings = Settings(_env_file=None, api_token="synthetic-trend-token-" + "x" * 32,
                        advisor_api_key="synthetic-only", data_dir=tmp_path / "state")
    advisor = IntegratedAdvisor(settings, model_factory=factory)
    for method in ("_root", "execute", "history"):
        monkeypatch.setattr(advisor, method, forbidden)
    advisor.analyze_trend("第一轮", [{"record_id": "first", "text": "FIRST_ONLY"}], {})
    advisor.analyze_trend("第二轮", [{"record_id": "second", "text": "SECOND_ONLY"}], {})
    assert len(calls) == 2 and all(len(value) == 2 for value in calls)
    assert "FIRST_ONLY" not in str(calls[1]) and "SECOND_ONLY" in str(calls[1])
    assert "不要输出行动建议" in calls[1][0]["content"]
    assert all(config.max_retries == 0 and config.data_dir is None for config in configurations)
    assert not settings.data_dir.exists()


def test_final_pipeline_uses_single_trend_entry(tmp_path):
    """最终管线只调用趋势方法一次，结果存档明确标记无记忆。"""
    calls = []

    class Advisor:
        """只实现新单次接口的模型替身。"""

        def analyze_trend(self, question, evidence, summary, *, demo=False):
            """记录本轮输入并返回趋势；由summarize调用。"""
            calls.append(question)
            return {"reply": {"content": "当前数据不足，无法判断发展趋势。", "usage": None},
                    "warnings": [], "mode": "model"}

    settings = Settings(_env_file=None, api_token="synthetic-summary-token-" + "x" * 32,
                        advisor_api_key="synthetic-only", data_dir=tmp_path / "state")
    paths = PipelinePaths(tmp_path / "output", organized=True)
    analysis = {"counts": {"selected_passages": 1, "candidate_facts": 0, "series_rows": 0}}
    modeling = {"status": "data_only", "series_reports": [], "requirements": []}
    summarize("本轮产业", analysis, modeling, [], CLIConfig(), settings, paths, advisor=Advisor())
    result = read_json(paths.file("llm"))
    assert len(calls) == 1 and result["status"] == "completed"
    assert result["memory_enabled"] is False and result["analysis_scope"] == "development_trend"
    assert "## 发展趋势" in paths.file("report").read_text("utf-8")
    assert not settings.data_dir.exists()
