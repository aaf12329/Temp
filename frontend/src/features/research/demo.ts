/** 演示数据：入口调用 fixture 构造器；所有数量和结论均为合成占位，不代表采集结果。 */
import type { DemoResult, ResearchInput, Scenario } from "@/types/research";

export const SAMPLE_INPUT: ResearchInput = {
  fileName: "示例网址.txt",
  urls: ["https://example.com/statistics", "https://example.org/report", "https://example.net/policy"],
  query: "整理贵阳餐饮业的收入、规模与发展变化",
};
export const PHASES = ["读取网址清单", "检查访问条件并采集", "匹配研究内容", "抽取、清洗与缺失诊断", "判断建模条件"];
export const DEMO_FILES = { urls: "网址清单示例.txt", csv: "整理结果_演示.csv", report: "分析报告_演示.md" };
export const DEMO_ROWS = [
  { year: "示例年份 A", metric: "示例指标", value: "100", source: "示例来源 A" },
  { year: "示例年份 B", metric: "示例指标", value: "110", source: "示例来源 B" },
];
export const DEMO_CSV = "\uFEFF年份,指标,数值,来源,备注\r\n" + DEMO_ROWS.map(
  (row) => `${row.year},${row.metric},${row.value},${row.source},合成演示数据`).join("\r\n");
export const DEMO_REPORT = "# 信息分析与建议（演示占位）\n\n此内容未调用 LLM，不是产业研究结论。\n\n建议补充同口径、带发布时间的连续真实数据，并逐条核对来源。\n";

/** 根据页面演示分支构造合成计数；不分析输入网址或推断真实可建模性。 */
export function makeDemoResult(input: ResearchInput, scenario: Scenario): DemoResult {
  const submitted = input.urls.length;
  const skipped = submitted > 1 ? 1 : 0;
  const canModel = scenario === "model-ready";
  return {
    submitted, skipped, collected: submitted - skipped, rows: canModel ? 40 : DEMO_ROWS.length, canModel,
    reasons: canModel ? [] : ["演示：同口径的连续年度不足。", "演示：真实样本不足，尚不满足建模门槛；不进入 WGAN 或 XGBoost。"],
  };
}
