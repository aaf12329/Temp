/** 流程共用层：initialState → ResearchFlow.update/reset → 状态快照。
 * 聚合入口调用；仅处理浏览器演示事件，不执行任务。门槛/选择 → 页面与分支。
 */
import type { FlowState, ModelChoice, Scenario, View } from "../types/research.ts";

export type FlowEvent =
  | { type: "reset" | "submit" | "next-data" | "start-model" | "next-gan" | "next-training" | "fail-training" | "retry-training" }
  | { type: "preview"; view: View }
  | { type: "navigate"; view: "result" | "model" | "evaluation" | "report" }
  | { type: "scenario"; scenario: Scenario }
  | { type: "choice"; choice: ModelChoice }
  | { type: "quality"; quality: "pass" | "fail" }
  | { type: "accept-gan"; accept: boolean };

/** 创建与重置共用的初始状态；5倍仅为可修改的前端演示选择，不修改训练JSON。 */
export function initialState(scenario: Scenario = "data-only"): FlowState {
  return { view: "input", scenario, phase: 0, choice: { useGan: true, framework: "pytorch", ratio: 5 },
    ganPhase: 0, ganAccepted: null, ganQuality: "pass", trainingPhase: 0, trainingError: false };
}

/** 封装流程判定；每次事件生成新快照，旧状态不被修改。 */
export class ResearchFlow {
  private state: FlowState;
  constructor(state: FlowState) { this.state = { ...state, choice: { ...state.choice } }; }

  /** 复用初始构造，避免重置后沿用旧GAN确认。 */
  reset(scenario: Scenario = "data-only"): FlowState { this.state = initialState(scenario); return this.state; }

  /** 消费聚合入口的动作；不可建模、未审查GAN及未完成训练均不能沿正常流程越级。 */
  update(event: FlowEvent): FlowState {
    const s = this.state;
    switch (event.type) {
      case "reset": return this.reset();
      case "scenario": return this.reset(event.scenario);
      case "submit": return { ...this.reset(s.scenario), view: "processing" };
      case "preview": {
        const later = !["input", "processing", "result", "report"].includes(event.view);
        const next = this.reset(later ? "model-ready" : s.scenario);
        return { ...next, view: event.view, ganPhase: event.view === "gan-review" ? 3 : 0,
          ganAccepted: ["training", "evaluation"].includes(event.view) || (event.view === "report" && next.scenario === "model-ready") ? true : null,
          trainingPhase: event.view === "evaluation" || event.view === "report" ? 3 : 0 };
      }
      case "navigate":
        if (event.view === "evaluation" && (s.trainingPhase < 3 || s.ganAccepted === null)) return s;
        if (event.view === "model" && s.scenario !== "model-ready") return s;
        if (event.view === "model") return { ...this.reset(s.scenario), view: "model" };
        return { ...s, view: event.view };
      case "next-data": return s.view !== "processing" ? s : { ...s, phase: Math.min(4, s.phase + 1), view: s.phase >= 4 ? "result" : "processing" };
      case "choice": return { ...s, choice: event.choice, ganAccepted: null, ganPhase: 0, trainingPhase: 0, trainingError: false };
      case "start-model":
        if (s.scenario !== "model-ready" || s.view !== "model") return s;
        return { ...s, view: s.choice.useGan ? "gan" : "training", ganAccepted: s.choice.useGan ? null : false };
      case "next-gan": return s.view !== "gan" ? s : { ...s, ganPhase: Math.min(3, s.ganPhase + 1), view: s.ganPhase >= 3 ? "gan-review" : "gan" };
      case "quality": return { ...s, ganQuality: event.quality, ganAccepted: null };
      case "accept-gan":
        if (s.view !== "gan-review" || (event.accept && s.ganQuality === "fail")) return s;
        return { ...s, view: "training", ganAccepted: event.accept, trainingPhase: 0, trainingError: false };
      case "next-training":
        if (s.view !== "training" || s.trainingError || s.ganAccepted === null) return s;
        return { ...s, trainingPhase: Math.min(3, s.trainingPhase + 1), view: s.trainingPhase >= 3 ? "evaluation" : "training" };
      case "fail-training": return s.view === "training" ? { ...s, trainingError: true } : s;
      case "retry-training": return { ...s, trainingPhase: 0, trainingError: false };
    }
  }
}

/** React reducer入口；只返回新状态，不运行副作用。 */
export function reduceFlow(state: FlowState, event: FlowEvent): FlowState { return new ResearchFlow(state).update(event); }
