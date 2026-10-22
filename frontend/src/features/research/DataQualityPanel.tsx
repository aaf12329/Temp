/** 数据质量展示叶子：父组件传入真实统计 → 计数与字段缺失对照，不计算模型可信度。 */
import type { DataQuality } from "@/types/pipeline";

/** 同时列出各阶段分母，避免把LLM增加候选理解为新增真实观测。 */
export function DataQualityPanel({ quality }: { quality: DataQuality }) {
  const stages: [string, number][] = [
    ["清洗前规则候选", quality.counts.raw_candidates],
    ["整理后候选", quality.counts.organized_candidates],
    ["通过检查的候选", quality.counts.usable_candidates],
    ["待复核记录", quality.counts.review_records],
    ["同口径年度行", quality.counts.annual_rows],
  ];
  const profiles = [quality.before, quality.after, quality.usable];
  return <section aria-labelledby="quality-heading">
    <h3 id="quality-heading">清洗前后数据核对</h3>
    <div className="table-scroll"><table><thead><tr><th>阶段</th><th>记录数</th></tr></thead>
      <tbody>{stages.map(([label, count]) => <tr key={label}><td>{label}</td><td>{count}</td></tr>)}</tbody>
    </table></div>
    <p>{quality.interpretation}</p>
    <p>整理后检测到 {quality.after.exact_duplicate_candidates} 条同口径、同值、同发布时点的重复候选；来源保留，年度序列按原有规则合并。</p>
    <details><summary>关键字段缺失与格式检查</summary>
      <div className="table-scroll"><table><thead><tr><th>字段</th>
        <th>清洗前（{quality.before.rows}条）</th><th>整理后（{quality.after.rows}条）</th><th>可用候选（{quality.usable.rows}条）</th>
      </tr></thead><tbody>{Object.entries(quality.before.fields).map(([field, stats]) => <tr key={field}>
        <td>{stats.label}</td>{profiles.map((profile, index) => <td key={index}>{profile.rows === 0 ? "无候选" : `缺失 ${profile.fields[field].missing}，格式异常 ${profile.fields[field].invalid}`}</td>)}
      </tr>)}</tbody></table></div>
    </details>
  </section>;
}
