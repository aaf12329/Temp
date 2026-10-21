/** 合成分析共用层：固定观测 → 指标/图坐标/导出文本。
 * 由聚合入口调用后传给页面；不读取实际数据，不拟合GAN、t-SNE、XGBoost或SHAP。
 */
import type { Metrics, Observation } from "../types/research.ts";

export const DEMO_SPLIT = { train: 24, validation: 8, test: 8, total: 40 };
export const GAN_PHASES = ["准备真实训练集", "WGAN 训练（5000轮配置示意）", "按比例生成并筛选", "准备 t-SNE 与质量检查"];
export const TRAIN_PHASES = ["固定训练 / 验证 / 测试划分", "拟合真实数据基准与所选方案", "在独立真实数据上评估", "整理性能图与解释"];
export const DEMO_OBSERVATIONS: Observation[] = [
  { year: 2012, actual: 102, baseline: 96, augmented: 99 }, { year: 2013, actual: 108, baseline: 112, augmented: 111 },
  { year: 2014, actual: 106, baseline: 111, augmented: 109 }, { year: 2015, actual: 117, baseline: 110, augmented: 112 },
  { year: 2016, actual: 122, baseline: 128, augmented: 126 }, { year: 2017, actual: 119, baseline: 125, augmented: 122 },
  { year: 2018, actual: 130, baseline: 122, augmented: 125 }, { year: 2019, actual: 135, baseline: 129, augmented: 131 },
];
export const DEMO_FILES_ANALYSIS = { predictions: "预测对照_演示.csv", metrics: "模型指标_演示.json", report: "完整分析报告_演示.md", tsne: "GAN分布布局_演示.svg" };

/** 以真实训练集计数算计划生成量；拒绝非有限值、负值与非法样本数。 */
export function generatedCount(realTrain: number, ratio: number): number {
  if (!Number.isSafeInteger(realTrain) || realTrain < 0 || !Number.isFinite(ratio) || ratio < 0) return 0;
  return Math.floor(realTrain * ratio);
}

/** 计算固定合成测试行的回归指标，保持图、表和下载一致；不是模型训练。 */
export function demoMetrics(rows: Observation[], augmented: boolean): Metrics {
  const mean = rows.reduce((sum, row) => sum + row.actual, 0) / rows.length;
  const errors = rows.map((row) => row.actual - (augmented ? row.augmented : row.baseline));
  const squareError = errors.reduce((sum, error) => sum + error ** 2, 0);
  const total = rows.reduce((sum, row) => sum + (row.actual - mean) ** 2, 0);
  return { mae: errors.reduce((sum, error) => sum + Math.abs(error), 0) / rows.length,
    rmse: Math.sqrt(squareError / rows.length), r2: total === 0 ? 0 : 1 - squareError / total };
}

/** 固定分布布局点；只是图形fixture，不是实际t-SNE嵌入。 */
export function demoEmbedding(count: number, offset = 0): [number, number][] {
  return Array.from({ length: count }, (_, index) => {
    const angle = index * 2.39996 + offset;
    const radius = 8 + Math.sqrt((index % 24) / 24) * 30;
    return [50 + Math.cos(angle) * radius, 50 + Math.sin(angle) * radius * .85];
  });
}

/** 输出带固定演示水印的SVG；浏览器下载与图中使用同一组布局点。 */
export function demoEmbeddingSvg(count: number): string {
  const points = [...demoEmbedding(DEMO_SPLIT.train).map(([x, y]) => `<circle cx="${x * 4}" cy="${y * 3}" r="3" fill="#2358ad"/>`),
    ...demoEmbedding(count, .7).map(([x, y]) => `<circle cx="${x * 4}" cy="${y * 3}" r="2" fill="#b25218" opacity=".55"/>`)];
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 340"><rect width="400" height="340" fill="white"/><text x="12" y="20" font-size="13">DEMO ONLY: layout fixture, not computed t-SNE</text>${points.join("")}<text x="12" y="325" font-size="12">Blue: simulated real train (24); orange: simulated generated (${count})</text></svg>`;
}

/** 导出选定方案的固定示例预测；每行保留演示标记，不伪造未来实测值。 */
export function demoPredictionsCsv(augmented: boolean): string {
  return "\uFEFF年份,示例观测值,示例预测值,残差,备注\r\n" + DEMO_OBSERVATIONS.map((row) => {
    const predicted = augmented ? row.augmented : row.baseline;
    return `${row.year},${row.actual},${predicted},${row.actual - predicted},合成演示非真实预测`;
  }).join("\r\n");
}

/** 构造与选择相符的文字报告；仅合成占位，未调用LLM。 */
export function demoAnalysisReport(query: string, modelReady: boolean, augmented: boolean, ratio: number): string {
  const metrics = demoMetrics(DEMO_OBSERVATIONS, augmented);
  return `# 产业信息分析报告（合成演示）\n\nLLM 未调用。没有采集、模型训练或真实产业结论。\n\n研究需求：${query}\n\n` +
    (modelReady ? `方案：${augmented ? `使用GAN，生成/真实训练比=${ratio}` : "仅真实数据（模拟）"}。\n示例真实训练/验证/测试：24/8/8；测试为合成fixture。\nMAE=${metrics.mae.toFixed(3)}；RMSE=${metrics.rmse.toFixed(3)}；R²=${metrics.r2.toFixed(3)}。\n\n` : "数据不足分支：不进入GAN或XGBoost，无模型指标。\n\n") +
    "## 分析与建议占位\n核对来源、统计口径及发布时间；补充独立真实数据；检查时间外推误差。\nSHAP解释模型行为，不表示因果关系。合成数据不增加独立真实证据。\n";
}
