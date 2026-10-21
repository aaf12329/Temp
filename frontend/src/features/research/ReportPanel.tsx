/** 报告功能：研究需求/所选方案/报告占位 → 汇总与下载回调。
 * ReportPanel → 文本与按钮；不调用LLM，不把页面选择当作真实证据。
 */
type Props = { query: string; modelReady: boolean; augmented: boolean; ratio: number; report: string; onReport: () => void; onCsv: () => void; onBack: () => void; onReset: () => void };

/** 显示数据不足/已建模两条路线的最终说明，模型失败不能冒充成功报告。 */
export function ReportPanel({ query, modelReady, augmented, ratio, report, onReport, onCsv, onBack, onReset }: Props) {
  return <section className="panel" aria-labelledby="report-title">
    <h2 id="report-title">信息分析与建议（演示）</h2><p>研究需求：{query}</p>
    <p className="notice"><strong>LLM 未调用。</strong> 下面是交给设计组检查结构的示例文案，不是真实产业结论。</p>
    <h3>本轮结果</h3><p>{modelReady ? `演示已完成建模，采用${augmented ? `GAN ${ratio}倍增强方案` : "仅真实数据方案"}。` : "演示数据未达到建模门槛，直接保留整理CSV，不进入GAN或XGBoost。"}</p>
    <h3>主要发现</h3><p>{modelReady ? "正式接入后，这里说明预测表现、与基准的差异、适用范围及未能解释的误差。" : "正式接入后，这里仅总结已提取的信息与证据缺口，不编造预测。"}</p>
    <h3>证据与限制</h3><ul><li>来源、发布时间和统计口径需要逐条核对。</li><li>合成样本不增加独立真实证据；图形重叠不代表预测可靠。</li><li>模型评估与建议应保留样本数量、时间范围和不确定性。</li></ul>
    <h3>下一步建议</h3><p>补充同口径来源，复核异常与缺失项，增加独立真实数据后再评估。</p>
    <details><summary>查看可下载报告文本</summary><pre className="report-text">{report}</pre></details>
    <div className="actions"><button onClick={onReport}>下载完整演示报告</button><button onClick={onCsv}>{modelReady ? "下载演示预测 CSV" : "下载演示整理 CSV"}</button><button onClick={onBack}>{modelReady ? "返回模型评估" : "返回整理结果"}</button><button onClick={onReset}>开始新的研究</button></div>
  </section>;
}
