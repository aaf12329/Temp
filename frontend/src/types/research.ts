/** 共用契约层：页面状态、输入和演示结果；没有绑定后端接口合同。 */
export type View = "input" | "processing" | "result" | "model" | "gan" | "gan-review" | "training" | "evaluation" | "report";
export type Scenario = "data-only" | "model-ready";
export type ResearchInput = { fileName: string; urls: string[]; query: string };
export type ParsedUrls = { urls: string[]; rejected: { line: number; reason: string }[] };
export type DemoResult = {
  submitted: number;
  collected: number;
  skipped: number;
  rows: number;
  canModel: boolean;
  reasons: string[];
};
export type ModelChoice = { useGan: boolean; framework: "pytorch" | "tensorflow"; ratio: number };
export type FlowState = {
  view: View; scenario: Scenario; phase: number; choice: ModelChoice;
  ganPhase: number; ganAccepted: boolean | null; ganQuality: "pass" | "fail";
  trainingPhase: number; trainingError: boolean;
};
export type Observation = { year: number; actual: number; baseline: number; augmented: number };
export type Metrics = { mae: number; rmse: number; r2: number };
