"use client";
/** 实际研究显式聚合入口：InputPanel → submitStage → 轮询 → LiveResults。
 * 仅接同源Node转发；Python持久Worker执行计算，用户选择前不启动GAN。
 * 数据流：浏览器TXT/需求 → 三阶段持久任务 → 实际图表/CSV/报告，无模拟结果兜底。
 */
import { useEffect, useState } from "react";
import { artifactUrl, queryOperation, submitStage } from "@/lib/platform-client";
import { downloadText } from "@/lib/download";
import type { PipelineOperation, PipelineProgress } from "@/types/pipeline";
import type { ResearchInput } from "@/types/research";
import { InputPanel } from "./InputPanel";
import { LiveResults } from "./LiveResults";
import { ResearchMessages } from "./ResearchMessages";

/** 保留最后完成结果供用户选择下一阶段；新任务失败不销毁已完成产物。 */
export function LiveResearch() {
  const [active, setActive] = useState<string | null>(null);
  const [completed, setCompleted] = useState<PipelineOperation | null>(null);
  const [status, setStatus] = useState("");
  const [progress, setProgress] = useState<PipelineProgress | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [framework, setFramework] = useState<"pytorch" | "tensorflow">("pytorch");
  const [llm, setLlm] = useState("api");
  const [extraction, setExtraction] = useState<"llm" | "deterministic">("llm");
  const [restore, setRestore] = useState("");
  const [pending, setPending] = useState<Record<string, unknown> | null>(null);
  const busy = submitting || active !== null;

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    /** 仅轮询状态；请求失败停止并给出恢复ID，不重复提交或训练。 */
    async function poll() {
      try {
        const operation = await queryOperation(active!, controller.signal);
        if (controller.signal.aborted) return;
        setProgress(operation.progress || null);
        setStatus(operation.state === "queued" ? "任务已排队，等待 Worker" : operation.progress?.message || "Worker 正在处理实际任务");
        if (operation.state === "done") { setCompleted(operation); setActive(null); setStatus("本阶段完成"); }
        else if (operation.state === "failed") { setError(operation.error?.message || "任务失败"); setActive(null); }
        else timer = setTimeout(poll, 1500);
      } catch (cause) {
        if (!controller.signal.aborted) { setError(`${cause instanceof Error ? cause.message : "查询失败"}；可用任务编号 ${active} 恢复查询。`); setActive(null); }
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [active]);

  /** 新动作只生成一次幂等ID；HTTP响应不明确时可重试相同载荷。 */
  async function submit(payload: Record<string, unknown>, retry = false) {
    if (busy) return;
    const request = retry ? payload : { ...payload, operation_id: crypto.randomUUID(), project_id: "local" };
    setSubmitting(true); setError(""); setPending(request); setProgress(null); setStatus("正在提交任务");
    try { const response = await submitStage(request); setActive(response.operation_id); setPending(null); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "提交失败"); }
    finally { setSubmitting(false); }
  }
  /** 读取真实用户输入，不访问示例网站或注入演示结果。 */
  function start(input: ResearchInput) { void submit({ stage: "prepare", urls: input.urls, question: input.query, llm, extraction }); }
  const result = completed?.result;
  return <>
    <header><h1>产业信息研究平台</h1><p>网址清单 → 信息整理 → 可选 WGAN → XGBoost → 发展趋势</p></header>
    <aside className="notice">实际研究模式：提交后会访问所列公开网址，受站点策略和 robots 等限制。匹配内容先保存 CSV，再按所选路径整理；最终趋势另行分析。两阶段均无会话记忆，清洗前后数据保留供核对。</aside>
    {!result && !busy && <>
      <label>数据抽取路径 <select value={extraction} onChange={(event) => setExtraction(event.target.value as "llm" | "deterministic")}><option value="llm">自动：LLM 清洗（原路径）</option><option value="deterministic">手动：配置规则确定性抽取（PDF 式）</option></select></label>
      <p className="muted">确定性路径按配置中的正则规则逐页取数，不调用清洗 API；版式固定的年度公报更稳。最终趋势分析仍按下方 LLM 设置执行。</p>
      <label>最终趋势分析 / 自动路径清洗 <select value={llm} onChange={(event) => setLlm(event.target.value)}><option value="api">使用已配置的 LLM API 清洗并分析趋势</option><option value="off">不调用 API，保留本地规则与数据报告</option><option value="demo">演示最终助手，清洗使用本地规则</option></select></label>
      <InputPanel live onSubmit={start} onExample={() => downloadText("网址清单模板.txt", "# 每行一个完整的公开HTTP(S)网址；请填写你要研究的实际来源。\n")} /></>}
    {busy && <section className="panel" aria-live="polite"><h2>正在处理实际任务</h2><p>{status || "正在提交任务"}</p><p>任务编号：<code>{active || String(pending?.operation_id || "提交中")}</code></p><p>采集和训练耗时取决于来源与配置。没有模拟进度；切换页面或关闭浏览器不会取消后台任务。</p></section>}
    {progress && <ResearchMessages progress={progress} />}
    {error && <div className="notice" role="alert"><p>{error}</p>{pending && <button disabled={busy} onClick={() => void submit(pending, true)}>重试同一提交</button>}</div>}
    {result && completed && <LiveResults result={result} files={Object.keys(result.files).map((name) => ({ name, url: artifactUrl(completed.operation_id, name) }))} busy={busy} framework={framework} onFramework={setFramework}
      onGan={() => void submit({ stage: "gan", parent_id: completed.operation_id, framework })} onModel={(accept) => void submit({ stage: "model", parent_id: completed.operation_id, framework, use_generated: accept })}
      onReset={() => { setCompleted(null); setError(""); setPending(null); setProgress(null); }} />}
    <details><summary>恢复已提交任务</summary><label htmlFor="restore-id">任务编号</label><input id="restore-id" maxLength={64} value={restore} onChange={(event) => setRestore(event.target.value)} /><button disabled={busy || !/^[A-Za-z0-9_-]{1,64}$/.test(restore)} onClick={() => { setError(""); setActive(restore); }}>恢复查询</button></details>
  </>;
}
