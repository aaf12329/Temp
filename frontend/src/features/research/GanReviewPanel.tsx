/** GAN确认功能：点坐标/检查状态 → 分布示意与用户选择。
 * GanReviewPanel → SVG/质量表/回调；不执行降维，失败检查时禁止采用。
 */
import { useId } from "react";
type Props = { real: [number, number][]; generated: [number, number][]; ratio: number; quality: "pass" | "fail"; onQuality: (value: "pass" | "fail") => void; onAccept: (accept: boolean) => void; onDownload: () => void; onBack: () => void };

/** 展示t-SNE预期位置与质量占位；选择权与生成阶段分离。 */
export function GanReviewPanel({ real, generated, ratio, quality, onQuality, onAccept, onDownload, onBack }: Props) {
  const id = useId();
  return <section className="panel" aria-labelledby="gan-review-title">
    <h2 id="gan-review-title">GAN 数据审查与采用确认（演示）</h2>
    <p>生成比例 {ratio} 倍；真实训练样本 {real.length} 条，生成样本 {generated.length} 条，验证/测试各8条且不参与 GAN。</p>
    <figure className="chart"><figcaption>t-SNE 对比图位置（布局示意）</figcaption>
      <svg viewBox="0 0 400 320" role="img" aria-labelledby={id}><title id={id}>真实训练与生成样本的合成布局，未执行t-SNE降维</title>
        <text x="16" y="20">演示布局 · 未执行 t-SNE</text>
        {generated.map(([x, y], index) => <circle key={`g${index}`} cx={x * 4} cy={y * 3} r="2.5" fill="#b25218" opacity=".55" />)}
        {real.map(([x, y], index) => <circle key={`r${index}`} cx={x * 4} cy={y * 3} r="4" fill="#2358ad" />)}
      </svg><ul className="legend"><li><span style={{ background: "#2358ad" }} />真实训练数据（模拟）</li><li><span style={{ background: "#b25218" }} />生成数据（模拟）</li></ul>
    </figure>
    <p>点重叠只帮助观察分布，不能单独证明生成数据可靠。正式结果还需结合数值检查与真实验证表现。</p>
    <details><summary>质量检查演示分支</summary><label htmlFor="gan-quality">选择检查结果示例：</label><select id="gan-quality" value={quality} onChange={(event) => onQuality(event.target.value as "pass" | "fail")}><option value="pass">通过初步检查</option><option value="fail">未通过初步检查</option></select></details>
    <div className="table-scroll"><table><caption>质量检查状态（全部为占位，无实际测量）</caption><thead><tr><th>项目</th><th>状态</th></tr></thead><tbody>
      <tr><td>取值范围与业务约束</td><td>{quality === "pass" ? "通过示例" : "失败示例：存在越界值"}</td></tr>
      <tr><td>分布、相关性与重复率</td><td>{quality === "pass" ? "通过示例，具体指标待接入" : "未通过示例"}</td></tr>
      <tr><td>可采用生成样本</td><td>{quality === "pass" ? generated.length : 0} 条（演示）</td></tr>
    </tbody></table></div>
    {quality === "fail" && <p role="alert" className="error">演示检查未通过，不能采用这批生成数据；可以继续仅用真实数据建模。</p>}
    <div className="actions"><button disabled={quality === "fail"} onClick={() => onAccept(true)}>使用 GAN 数据进入 XGBoost</button><button onClick={() => onAccept(false)}>不用 GAN 数据，继续 XGBoost</button><button onClick={onDownload}>下载分布示意 SVG</button><button onClick={onBack}>返回调整选择</button></div>
  </section>;
}
