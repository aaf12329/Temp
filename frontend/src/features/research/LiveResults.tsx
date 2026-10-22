/** 实际结果叶子：服务返回的状态/文件URL → 门槛、GAN审查、性能图与报告。
 * LiveResearch注入数据和动作；不混用demo fixture，不访问本机磁盘。
 */
import type { PipelineResult } from "@/types/pipeline";
import { DataQualityPanel } from "./DataQualityPanel";
type Props = { result: PipelineResult; files: { name: string; url: string }[]; busy: boolean; framework: "pytorch" | "tensorflow"; onFramework: (value: "pytorch" | "tensorflow") => void; onRoute: (value: "llm" | "textbook") => void; onGan: () => void; onModel: (accept: boolean) => void; onReset: () => void };
const CHART_NAMES: Record<string, string> = { "evaluation.png": "预测对照、残差与误差分布", "importance.png": "XGBoost原生特征重要性", "trend.png": "真实测试期趋势与探索性预测", "missing.png": "数据缺失情况", "data_quality.png": "清洗前后记录数与字段缺失对照", "comparison.png": "模型性能对比", "shap.png": "TreeSHAP特征解释", "gan_tsne.png": "真实训练数据与生成数据 t-SNE", "gan.png": "WGAN训练曲线" };

/** 展示真实操作产物，未生成的回测图不显示占位；失败原因保持原样可查。 */
export function LiveResults({ result, files, busy, framework, onFramework, onRoute, onGan, onModel, onReset }: Props) {
  const images = files.filter((file) => file.name.endsWith(".png"));
  if (result.stage === "branch_choice" || result.stage === "materials_ready") return <section className="panel" aria-labelledby="collection-title">
    <h2 id="collection-title">{result.stage === "branch_choice" ? "网页采集完成：请选择后续分支" : "教材分支：先查看已采集材料"}</h2>
    <p>{result.context.question}</p>
    <p className="notice">已采集 {result.counts.documents} 份材料，失败或跳过 {result.counts.failed_urls} 项。目前只完成采集，尚未进行向量匹配、数值抽取、数据清洗、训练或 LLM 分析。</p>
    {result.stage === "branch_choice" ? <div className="actions">
      <button disabled={busy} onClick={() => onRoute("llm")}>继续原自动流程</button>
      <button disabled={busy} onClick={() => onRoute("textbook")}>进入教材分支，先查看材料</button>
    </div> : <p>本分支暂时到这里。后续如何按网页结构解析、提取和清洗，待查看材料后确定。</p>}
    <details open><summary>已采集来源（{result.collection?.documents.length || 0}份）</summary><ul>{result.collection?.documents.map((item) => <li key={item.record_id}>{item.title || item.url}：{item.raw_available ? "原文件和正文已保存" : "已保存正文；本来源未保存原文件"}</li>)}</ul></details>
    <details><summary>访问记录与失败原因（{result.access.length}项）</summary><ul>{result.access.map((item, index) => <li key={index}>{item.url}：{item.status} / {item.reason}</li>)}</ul></details>
    <p>sources.zip 包含已保存原件、正文和带来源及哈希的索引。材料包中 raw/ 为原件，normalized/ 为爬虫整理的正文等文件。</p>
    <ul>{files.map((file) => <li key={file.name}><a href={file.url}>{file.name}</a></li>)}</ul>
    <button disabled={busy} onClick={onReset}>开始新的采集</button>
  </section>;
  return <section className="panel" aria-labelledby="live-result-title">
    <h2 id="live-result-title">{result.stage === "choice" ? "信息整理完成：选择建模方式" : result.stage === "gan_review" ? (result.gan_usable ? "GAN数据审查：查看并决定是否采用" : "GAN阶段结果：未取得可采用数据，请查看原因") : "研究结果与分析"}</h2>
    <p>{result.context.question}</p>
    <p className="notice">数据抽取路径：{result.extraction === "deterministic" ? "手动/确定性规则（PDF 式），未调用 LLM 清洗" : "自动/LLM 清洗（原路径）"}。{result.extraction_detail?.rule_count ? `本轮规则 ${result.extraction_detail.rule_count} 条。` : ""}</p>
    {result.extraction_detail?.issues && result.extraction_detail.issues.length > 0 && <details><summary>确定性规则未采纳 {result.extraction_detail.issues.length} 项</summary><ul>{result.extraction_detail.issues.slice(0, 20).map((item, index) => <li key={index}>{item.message}</li>)}</ul></details>}
    {result.warning && <p className="notice">{result.warning}</p>}
    {result.llm_cleaning && (result.extraction === "deterministic"
      ? <p className="notice">本轮未调用 LLM 清洗：{result.llm_cleaning.reason}</p>
      : <p className="notice">LLM 数据清洗：{result.llm_cleaning.status}。{result.llm_cleaning.reason} 清洗前内容与待复核记录可在下方下载 llm_input.csv、review.csv。</p>)}
    {result.data_quality && <DataQualityPanel quality={result.data_quality} />}
    <dl className="stats">{Object.entries(result.counts).filter(([name]) => ["documents", "selected_passages", "candidate_facts", "series_rows"].includes(name)).map(([name, value]) => <div key={name}><dt>{{ documents: "采集材料", selected_passages: "匹配段落", candidate_facts: "数值候选", series_rows: "同口径年度行" }[name]}</dt><dd>{value}</dd></div>)}</dl>
    <details><summary>来源访问记录与跳过原因（{result.access.length}项）</summary><ul className="log">{result.access.map((item, index) => <li key={index}>{item.url}：{item.status} / {item.reason}</li>)}</ul></details>
    {result.diagnostics && <div className="notice"><strong>数据诊断：{result.diagnostics.message}</strong>
      {result.diagnostics.rejection_reasons.length > 0 && <ul>{result.diagnostics.rejection_reasons.map((item) => <li key={item.code}>{item.message}：{item.count}条</li>)}</ul>}
    </div>}
    {result.modeling.gan_gate && <p className="notice">{result.modeling.gan_gate.reason} {result.modeling.gan_gate.sample_definition}</p>}
    {!result.can_model && <div className="notice"><strong>未满足建模条件，直接导出已有信息。</strong><ul>{result.modeling.requirements.map((reason) => <li key={reason}>{reason}</li>)}{result.modeling.series_reports.flatMap((item) => item.requirements).map((reason, index) => <li key={index}>{reason}</li>)}</ul></div>}
    {result.modeling.series_reports.map((item) => <article key={item.directory} className="series-result"><h3>{item.scope}</h3>
      <p>状态：{item.status}。{item.split && `真实训练 ${item.split.train} / 验证 ${item.split.validation} / 测试 ${item.split.test}。`}</p>
      {item.gan.requested_synthetic_rows !== undefined && <p>计划生成 {item.gan.requested_synthetic_rows} 条（真实训练数×5），实际保留 {item.gan.synthetic_rows ?? 0} 条。</p>}
      {item.gan.reason && <p>GAN：{item.gan.reason}</p>}
      {item.gan.user_accepted && !item.gan.used_for_final_model && result.stage === "completed" && <p className="notice">已确认尝试增强，但真实验证未改善或该序列未通过检查，最终采用仅真实数据模型。</p>}
      {item.test && <><p>最终模型：{item.selected_model}。指标仅来自独立真实测试集，生成样本不参与测试。</p><div className="table-scroll"><table><thead><tr><th>方案</th><th>MAE</th><th>RMSE</th><th>R²</th><th>真实测试数</th></tr></thead><tbody>{Object.entries({ "所选方案": item.test.selected, "仅真实数据XGB": item.test.real_only, "沿用上期值": item.test.naive }).map(([label, metrics]) => <tr key={label}><td>{label}</td><td>{metrics.mae.toFixed(3)}</td><td>{metrics.rmse.toFixed(3)}</td><td>{metrics.r2 == null ? "不适用" : metrics.r2.toFixed(3)}</td><td>{metrics.n}</td></tr>)}</tbody></table></div></>}
      {item.test && item.test.selected.mae >= item.test.naive.mae && <p className="notice">真实测试上的误差未优于沿用上期值，暂不建议依赖该模型作预测判断。</p>}
      {item.forecast && <p>探索性预测：{item.forecast.target_period} 年，{item.forecast.prediction.toFixed(3)}。该值不是来源事实，也没有校准的预测区间。</p>}
      {item.requirements.length > 0 && <ul>{item.requirements.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
    </article>)}
    {result.stage === "choice" && <><p>生成量固定为真实训练集的5倍；先选择是否训练GAN，训练后还会单独确认是否采用。其他参数沿用服务端JSON。</p>
      {result.gan_available ? <label>WGAN框架 <select value={framework} disabled={busy} onChange={(event) => onFramework(event.target.value as "pytorch" | "tensorflow")}><option value="pytorch">PyTorch</option><option value="tensorflow">TensorFlow</option></select></label> : <p>本批数据可尝试XGBoost，但真实训练量尚未达到GAN门槛。</p>}
      <div className="actions"><button disabled={busy || !result.gan_available} onClick={onGan}>训练 WGAN，生成后先审查</button><button disabled={busy} onClick={() => onModel(false)}>不训练 GAN，直接 XGBoost</button></div></>}
    {result.stage === "gan_review" && <p className="notice">t-SNE只辅助观察分布，重叠不证明质量。采用后仍与仅真实数据模型比较，若真实验证没有改善则回退。</p>}
    {images.length > 0 && <div className="chart-grid">{images.map((image) => { const label = CHART_NAMES[image.name.split("/").at(-1)!] || image.name; return <figure className="chart" key={image.name}><figcaption>{label}</figcaption><a href={image.url} target="_blank" rel="noreferrer"><img src={image.url} alt={`本轮实际计算：${label}`} loading="lazy" style={{ width: "100%", height: "auto" }} /></a></figure>; })}</div>}
    {result.stage === "gan_review" && <div className="actions"><button disabled={busy || !result.gan_usable} onClick={() => onModel(true)}>采用通过检查的 GAN 数据，开始 XGBoost</button><button disabled={busy} onClick={() => onModel(false)}>不采用 GAN 数据，仅用真实数据建模</button></div>}
    {result.stage === "completed" && <><h3>发展趋势</h3>
      {result.llm?.reply?.content ? <pre className="report-text">{result.llm.reply.content}</pre> : <p>本轮未生成发展趋势分析。{result.llm?.reason || (result.llm?.status === "disabled" ? "本轮已关闭 LLM。" : "")}</p>}
    </>}
    <details><summary>下载本轮实际产物（{files.length}项）</summary><ul>{files.map((file) => <li key={file.name}><a href={file.url}>{file.name}</a></li>)}</ul></details>
    <div className="actions"><button disabled={busy} onClick={onReset}>开始新的研究</button></div>
  </section>;
}
