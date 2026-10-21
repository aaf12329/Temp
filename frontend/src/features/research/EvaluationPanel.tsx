/** 评估功能：固定测试行/指标 → 图表、明细与下载动作。
 * EvaluationPanel → 共用SVG绘图；合成fixture → 六种评估位置，不训练或调参。
 */
import { AnalysisChart, BarChart } from "@/components/AnalysisChart";
import type { Metrics, Observation } from "@/types/research";
type Props = { rows: Observation[]; selected: Metrics; baseline: Metrics; naive: Metrics; augmented: boolean; ratio: number; onCsv: () => void; onMetrics: () => void; onReport: () => void; onBack: () => void };

/** 展示预测表现与解释；分开量纲，所有测试行与导出使用同一fixture。 */
export function EvaluationPanel({ rows, selected, baseline, naive, augmented, ratio, onCsv, onMetrics, onReport, onBack }: Props) {
  const predictions = rows.map((row): [number, number] => [row.year, augmented ? row.augmented : row.baseline]);
  const models = [{ label: "沿用上期值基准", metrics: naive }, { label: "仅真实数据 XGB", metrics: baseline }, ...(augmented ? [{ label: "GAN + XGB", metrics: selected }] : [])];
  return <section className="panel" aria-labelledby="evaluation-title">
    <h2 id="evaluation-title">XGBoost 模型评估与解释（演示）</h2>
    <p>当前方案：{augmented ? `GAN增强 ${ratio} 倍（已确认采用）` : "仅真实训练数据，未使用GAN"}。</p>
    <div className="notice">所有图与数字来自固定合成示例，没有训练模型。示例测试集8条（2012—2019）；真实项目测试集必须独立且不含生成样本。</div>
    <dl className="stats"><div><dt>MAE（示例指数）</dt><dd>{selected.mae.toFixed(3)}</dd></div><div><dt>RMSE（示例指数）</dt><dd>{selected.rmse.toFixed(3)}</dd></div><div><dt>R²</dt><dd>{selected.r2.toFixed(3)}</dd></div><div><dt>测试记录（合成）</dt><dd>{rows.length} 条</dd></div></dl>
    <p className="muted">MAE/RMSE越小越好；R²不是可信度百分比。此演示恰好呈现增强改善，不表示真实训练一定改善。</p>
    <div className="chart-grid">
      <AnalysisChart title="1. 真实值—预测值对比" xLabel="示例观测值" yLabel="示例预测值" diagonal series={[{ name: "独立测试（合成）", color: "#2358ad", points: rows.map((row) => [row.actual, augmented ? row.augmented : row.baseline]) }]} />
      <AnalysisChart title="2. 按时间排列的预测对照" xLabel="示例年份" yLabel="示例产业规模指数" series={[{ name: "观测值（合成）", color: "#555", line: true, points: rows.map((row) => [row.year, row.actual]) }, { name: "预测值（合成）", color: "#2358ad", line: true, points: predictions }]} />
      <AnalysisChart title="3. 预测残差诊断" xLabel="示例预测值" yLabel="观测值－预测值" zero series={[{ name: "测试残差（合成）", color: "#b25218", points: rows.map((row) => { const prediction = augmented ? row.augmented : row.baseline; return [prediction, row.actual - prediction]; }) }]} />
      <BarChart title="4. 同一真实测试集的方案对比（模拟）" unit="RMSE，越低越好" rows={models.map((model) => ({ label: model.label, value: model.metrics.rmse }))} />
      <BarChart title="5. 按时间回测误差的位置示意" unit="RMSE占位，未执行回测" rows={[{ label: "历史窗口 A", value: 4.8 }, { label: "历史窗口 B", value: 6.2 }, { label: "历史窗口 C", value: 5.1 }]} />
      <BarChart title="6. SHAP特征解释的位置示意" unit="平均绝对SHAP占位，未计算" rows={[{ label: "上期指数", value: 3.4 }, { label: "历史变化率", value: 1.8 }, { label: "时间特征", value: .7 }]} />
    </div>
    <p>回测与SHAP条形是独立的布局占位，不能视为本次模型的计算结果。SHAP解释模型行为，不证明因果关系。</p>
    <div className="table-scroll"><table><caption>方案指标对照（同一组8条合成测试行）</caption><thead><tr><th>方案</th><th>MAE ↓</th><th>RMSE ↓</th><th>R²</th></tr></thead><tbody>{models.map((model) => <tr key={model.label}><td>{model.label}</td><td>{model.metrics.mae.toFixed(3)}</td><td>{model.metrics.rmse.toFixed(3)}</td><td>{model.metrics.r2.toFixed(3)}</td></tr>)}</tbody></table></div>
    <details><summary>查看8条预测明细（合成）</summary><div className="table-scroll"><table><thead><tr><th>年份</th><th>观测值</th><th>预测值</th><th>残差</th></tr></thead><tbody>{rows.map((row) => { const predicted = augmented ? row.augmented : row.baseline; return <tr key={row.year}><td>{row.year}</td><td>{row.actual}</td><td>{predicted}</td><td>{row.actual - predicted}</td></tr>; })}</tbody></table></div></details>
    <div className="actions"><button onClick={onReport}>查看最终分析与建议</button><button onClick={onCsv}>下载演示预测 CSV</button><button onClick={onMetrics}>下载演示指标 JSON</button><button onClick={onBack}>返回建模选择</button></div>
  </section>;
}
