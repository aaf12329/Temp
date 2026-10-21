"use client";

/** 显式聚合入口：ResearchPrototype → 输入/处理/结果/选择/训练/审查/评估/报告。
 * 共用流程类管理状态；共用fixture和下载I/O向叶子组件注入数据与回调。
 * TXT+需求 → 演示整理 → GAN人工确认或跳过 → XGBoost评估 → 报告；全程无后端。
 */
import { useReducer, useState } from "react";
import { downloadText } from "@/lib/download";
import { initialState, reduceFlow } from "@/lib/research-flow";
import { DEMO_FILES_ANALYSIS, DEMO_OBSERVATIONS, DEMO_SPLIT, GAN_PHASES, TRAIN_PHASES, demoAnalysisReport, demoEmbedding, demoEmbeddingSvg, demoMetrics, demoPredictionsCsv, generatedCount } from "@/lib/demo-analysis";
import type { ResearchInput, Scenario, View } from "@/types/research";
import { DEMO_CSV, DEMO_FILES, DEMO_REPORT, DEMO_ROWS, PHASES, SAMPLE_INPUT, makeDemoResult } from "./demo";
import { InputPanel } from "./InputPanel";
import { ProgressPanel } from "./ProgressPanel";
import { ResultPanel } from "./ResultPanel";
import { ModelPanel } from "./ModelPanel";
import { TrainingPanel } from "./TrainingPanel";
import { GanReviewPanel } from "./GanReviewPanel";
import { EvaluationPanel } from "./EvaluationPanel";
import { ReportPanel } from "./ReportPanel";

const VIEWS: { view: View; label: string }[] = [
  { view: "input", label: "1. 输入" }, { view: "processing", label: "2. 处理过程" },
  { view: "result", label: "3. 整理结果" }, { view: "model", label: "4. 建模选择" },
  { view: "gan", label: "5. GAN生成" }, { view: "gan-review", label: "6. t-SNE与确认" },
  { view: "training", label: "7. XGBoost建模" }, { view: "evaluation", label: "8. 模型评估" },
  { view: "report", label: "9. 分析与建议" },
];

/** 编排九个视图与可跳转预览；所有执行动作都只是浏览器状态变更。 */
export function ResearchPrototype() {
  const [state, dispatch] = useReducer(reduceFlow, undefined, () => initialState());
  const [input, setInput] = useState<ResearchInput>(SAMPLE_INPUT);
  const { view, scenario, choice } = state;
  const result = makeDemoResult(input, scenario);
  const generated = generatedCount(DEMO_SPLIT.train, choice.ratio);
  const augmented = state.ganAccepted === true;
  const modeled = scenario === "model-ready" && state.trainingPhase >= 3 && state.ganAccepted !== null;
  const metrics = demoMetrics(DEMO_OBSERVATIONS, augmented);
  const baseline = demoMetrics(DEMO_OBSERVATIONS, false);
  const naiveRows = DEMO_OBSERVATIONS.map((row, index) => ({ ...row, baseline: index ? DEMO_OBSERVATIONS[index - 1].actual : 100 }));
  const report = demoAnalysisReport(input.query, modeled, augmented, choice.ratio);

  /** 清理当前输入及确认状态，不删除任何文件。 */
  function reset() { setInput(SAMPLE_INPUT); dispatch({ type: "reset" }); }
  /** 共用预测下载入口；文件名与内容始终带演示标识。 */
  function predictions() { downloadText(DEMO_FILES_ANALYSIS.predictions, demoPredictionsCsv(augmented), "text/csv;charset=utf-8"); }
  /** 返回建模选择，主动清除旧训练和GAN确认，避免结果沿用。 */
  function backToModel() { dispatch({ type: "navigate", view: "model" }); }

  return <main>
    <header><h1>产业信息研究平台</h1><p className="muted">独立前端交互原型 · 从网址输入到最终分析</p></header>
    <aside className="notice"><strong>独立演示，未接后端。</strong> 文件只在浏览器读取；没有采集、模型训练或 LLM 请求。所有图、指标与结论均为合成占位，刷新后清除当前选择。</aside>
    <details className="demo" open><summary>页面预览与演示分支</summary>
      <p className="muted">可直接预览九个页面。跳转会重置演示选择；训练后页面使用预设演示状态。正常使用请按下方流程推进。</p>
      <div className="actions">{VIEWS.map((item) => <button key={item.view} aria-current={view === item.view ? "page" : undefined} onClick={() => dispatch({ type: "preview", view: item.view })}>{item.label}</button>)}</div>
      <label className="field" htmlFor="demo-scenario">演示分支（切换会回到输入并清除本轮结果）</label>
      <select id="demo-scenario" value={scenario} onChange={(event) => dispatch({ type: "scenario", scenario: event.target.value as Scenario })}>
        <option value="data-only">数据不足 → CSV 与文字结果</option><option value="model-ready">达到条件 → GAN可选 → XGBoost → 分析建议</option>
      </select>
    </details>
    <ol className="steps" aria-label="页面流程">{VIEWS.map((item) => <li key={item.view} aria-current={item.view === view ? "step" : undefined}>{item.label}</li>)}</ol>
    {view === "input" && <InputPanel onExample={() => downloadText(DEMO_FILES.urls, SAMPLE_INPUT.urls.join("\n"))} onSubmit={(next) => { setInput(next); dispatch({ type: "submit" }); }} />}
    {view === "processing" && <ProgressPanel input={input} phases={PHASES} phase={state.phase} onCancel={reset} onNext={() => dispatch({ type: "next-data" })} />}
    {view === "result" && <ResultPanel input={input} result={result} rows={DEMO_ROWS}
      onCsv={() => downloadText(DEMO_FILES.csv, DEMO_CSV, "text/csv;charset=utf-8")} onReport={() => downloadText(DEMO_FILES.report, DEMO_REPORT)} onReset={reset} onModel={backToModel} onFinal={() => dispatch({ type: "navigate", view: "report" })} />}
    {view === "model" && <ModelPanel choice={choice} trainCount={DEMO_SPLIT.train} generated={generated} onChange={(next) => dispatch({ type: "choice", choice: next })} onConfirm={() => dispatch({ type: "start-model" })} onResult={() => dispatch({ type: "navigate", view: "result" })} />}
    {view === "gan" && <TrainingPanel kind="gan" phases={GAN_PHASES} phase={state.ganPhase} description={`${choice.framework === "pytorch" ? "PyTorch" : "TensorFlow"}；24条训练样本 × ${choice.ratio}，计划生成${generated}条（合成演示）。`} onNext={() => dispatch({ type: "next-gan" })} onBack={backToModel} />}
    {view === "gan-review" && <GanReviewPanel real={demoEmbedding(DEMO_SPLIT.train)} generated={demoEmbedding(generated, .7)} ratio={choice.ratio} quality={state.ganQuality}
      onQuality={(quality) => dispatch({ type: "quality", quality })} onAccept={(accept) => dispatch({ type: "accept-gan", accept })} onBack={backToModel}
      onDownload={() => downloadText(DEMO_FILES_ANALYSIS.tsne, demoEmbeddingSvg(generated), "image/svg+xml;charset=utf-8")} />}
    {view === "training" && <TrainingPanel kind="xgboost" phases={TRAIN_PHASES} phase={state.trainingPhase} error={state.trainingError}
      description={augmented ? `采用GAN：24条模拟真实训练样本 + ${generated}条模拟生成样本；保留仅真实数据基准。` : "仅使用24条模拟真实训练样本；没有采用GAN数据。"}
      onNext={() => dispatch({ type: "next-training" })} onFail={() => dispatch({ type: "fail-training" })} onRetry={() => dispatch({ type: "retry-training" })} onBack={backToModel} />}
    {view === "evaluation" && <EvaluationPanel rows={DEMO_OBSERVATIONS} selected={metrics} baseline={baseline} naive={demoMetrics(naiveRows, false)} augmented={augmented} ratio={choice.ratio}
      onCsv={predictions} onMetrics={() => downloadText(DEMO_FILES_ANALYSIS.metrics, JSON.stringify({ demo: true, trained: false, evaluation: "fixed synthetic fixture", split: DEMO_SPLIT, useGan: augmented, ratio: augmented ? choice.ratio : 0, selected: metrics, baseline }, null, 2), "application/json;charset=utf-8")}
      onReport={() => dispatch({ type: "navigate", view: "report" })} onBack={backToModel} />}
    {view === "report" && <ReportPanel query={input.query} modelReady={modeled} augmented={augmented} ratio={choice.ratio} report={report}
      onReport={() => downloadText(DEMO_FILES_ANALYSIS.report, report)} onCsv={modeled ? predictions : () => downloadText(DEMO_FILES.csv, DEMO_CSV, "text/csv;charset=utf-8")}
      onBack={() => dispatch({ type: "navigate", view: modeled ? "evaluation" : "result" })} onReset={reset} />}
  </main>;
}
