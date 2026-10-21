/** 结果功能：合成结果与占位文本 → 结果展示和下载回调；不推断真实产业结论。 */
import type { DemoResult, ResearchInput } from "@/types/research";

type Props = {
  input: ResearchInput; result: DemoResult; rows: { year: string; metric: string; value: string; source: string }[];
  onCsv: () => void; onReport: () => void; onReset: () => void; onModel: () => void; onFinal: () => void;
};

/** 呈现数据不足退路和示例文件入口；由原型入口提供已标注的演示数据。 */
export function ResultPanel({ input, result, rows, onCsv, onReport, onReset, onModel, onFinal }: Props) {
  return <section className="panel" aria-labelledby="result-title">
    <h2 id="result-title">信息整理完成（演示）</h2>
    <p>{input.query}</p>
    <p>{result.canModel ? "演示分支：数据达到条件，尚未开始建模。" : "演示分支：数据暂不满足建模条件，保留已有整理结果。"}</p>
    <dl className="stats">
      <div><dt>提交网址</dt><dd>{result.submitted}</dd></div><div><dt>采集成功（合成）</dt><dd>{result.collected}</dd></div>
      <div><dt>跳过或失败（合成）</dt><dd>{result.skipped}</dd></div><div><dt>数据行（合成）</dt><dd>{result.rows}</dd></div>
    </dl>
    {!result.canModel && <><h3>为什么不能建模？</h3><ul>{result.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></>}
    <h3>数据文件</h3>
    <p className="muted">以下两行与整理CSV均为合成预览，不是从所选网站提取的结果；建模分支的40条记录仅为另一组流程占位，不包含在此两行文件中。</p>
    <div className="table-scroll"><table><caption>整理数据预览（演示）</caption><thead><tr><th>年份</th><th>指标</th><th>数值</th><th>来源</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.year}><td>{row.year}</td><td>{row.metric}</td><td>{row.value}</td><td>{row.source}</td></tr>)}</tbody></table></div>
    <div className="actions"><button onClick={onCsv}>下载演示 CSV</button></div>
    <h3>信息分析与建议</h3>
    <p><strong>LLM 未调用：这里是文字占位。</strong></p>
    <p>后续在此展示主要发现、来源与口径限制、证据不足之处，以及补充数据的建议。</p>
    <div className="actions"><button onClick={onReport}>下载演示报告</button>{result.canModel ? <button onClick={onModel}>继续建模选择</button> : <button onClick={onFinal}>查看最终分析与建议</button>}<button onClick={onReset}>返回首页</button></div>
  </section>;
}
