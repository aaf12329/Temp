"use client";

/** 显式聚合入口：页面状态 → 四个功能组件；输入与 fixture 单独管理，不连接后端。
 * 调用：Home → ResearchPrototype → Input/Progress/Result/Model；下载走浏览器共用 I/O。
 * 数据流：TXT + 一句话 → 本地演示阶段 → 演示结果或训练前选择，全程仅保存在内存。
 */
import { useState } from "react";
import { downloadText } from "@/lib/download";
import type { ModelChoice, ResearchInput, Scenario, View } from "@/types/research";
import { DEMO_CSV, DEMO_FILES, DEMO_REPORT, DEMO_ROWS, PHASES, SAMPLE_INPUT, makeDemoResult } from "./demo";
import { InputPanel } from "./InputPanel";
import { ProgressPanel } from "./ProgressPanel";
import { ResultPanel } from "./ResultPanel";
import { ModelPanel } from "./ModelPanel";

const VIEWS: { view: View; label: string }[] = [
  { view: "input", label: "1. 输入" }, { view: "processing", label: "2. 处理过程" },
  { view: "result", label: "3. 整理结果" }, { view: "model", label: "4. 建模选择" },
];
const INITIAL_CHOICE: ModelChoice = { useGan: true, framework: "pytorch" };

/** 编排四个页面状态与预览入口；不包含网络请求、真实预测或后台计时任务。 */
export function ResearchPrototype() {
  const [view, setView] = useState<View>("input");
  const [input, setInput] = useState<ResearchInput>(SAMPLE_INPUT);
  const [scenario, setScenario] = useState<Scenario>("data-only");
  const [phase, setPhase] = useState(0);
  const [choice, setChoice] = useState<ModelChoice>(INITIAL_CHOICE);
  const [confirmed, setConfirmed] = useState(false);
  const result = makeDemoResult(input, scenario);

  /** 清除本轮内存状态后返回首页；不改磁盘文件。 */
  function reset() {
    setView("input"); setInput(SAMPLE_INPUT); setPhase(0); setChoice(INITIAL_CHOICE); setConfirmed(false);
  }

  return <main>
    <header><h1>产业信息研究平台</h1><p className="muted">前端结构原型 · 到 GAN 训练之前</p></header>
    <aside className="notice"><strong>独立演示，未接后端。</strong> 文件只在浏览器读取；没有采集、模型训练或 LLM 请求。所有结果均为合成占位。</aside>
    <details className="demo"><summary>页面预览与演示分支</summary>
      <p className="muted">预览按钮可直接查看布局；未提交文件时使用示例输入。</p>
      <div className="actions">{VIEWS.map((item) => <button key={item.view} aria-current={view === item.view ? "page" : undefined} onClick={() => {
        setView(item.view); setConfirmed(false);
        if (item.view === "model") setScenario("model-ready");
        if (item.view === "processing") setPhase(0);
      }}>{item.label}</button>)}</div>
      <label className="field" htmlFor="demo-scenario">演示分支</label>
      <select id="demo-scenario" value={scenario} onChange={(event) => setScenario(event.target.value as Scenario)}>
        <option value="data-only">数据不足 → CSV 与文字结果</option><option value="model-ready">达到条件 → 建模选择</option>
      </select>
    </details>
    <ol className="steps" aria-label="页面流程">{VIEWS.map((item) => <li key={item.view} aria-current={item.view === view ? "step" : undefined}>{item.label}</li>)}</ol>
    {view === "input" && <InputPanel onExample={() => downloadText(DEMO_FILES.urls, SAMPLE_INPUT.urls.join("\n"))} onSubmit={(nextInput) => {
      setInput(nextInput); setPhase(0); setChoice(INITIAL_CHOICE); setConfirmed(false); setView("processing");
    }} />}
    {view === "processing" && <ProgressPanel input={input} phases={PHASES} phase={phase} onCancel={reset} onNext={() => {
      if (phase < PHASES.length - 1) setPhase(phase + 1);
      else setView(result.canModel ? "model" : "result");
    }} />}
    {view === "result" && <ResultPanel input={input} result={result} rows={DEMO_ROWS}
      onCsv={() => downloadText(DEMO_FILES.csv, DEMO_CSV, "text/csv;charset=utf-8")}
      onReport={() => downloadText(DEMO_FILES.report, DEMO_REPORT)} onReset={reset} onModel={() => setView("model")} />}
    {view === "model" && <ModelPanel choice={choice} confirmed={confirmed} onChange={(next) => { setChoice(next); setConfirmed(false); }}
      onConfirm={() => setConfirmed(true)} onResult={() => setView("result")} onReset={reset} />}
  </main>;
}
