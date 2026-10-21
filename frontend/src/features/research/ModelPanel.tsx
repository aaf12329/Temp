/** 建模前选择：用户选项 → 内存回调；本阶段不加载训练器或执行 XGBoost/GAN。 */
import type { ModelChoice } from "@/types/research";

type Props = { choice: ModelChoice; trainCount: number; generated: number; onChange: (choice: ModelChoice) => void; onConfirm: () => void; onResult: () => void };

/** 用户选择是否尝试GAN、框架和增强比；采用生成数据要在后续另行确认。 */
export function ModelPanel({ choice, trainCount, generated, onChange, onConfirm, onResult }: Props) {
  return <section className="panel" aria-labelledby="model-title">
    <h2 id="model-title">建模选择（演示）</h2>
    <p>这里演示“真实数据达到条件”的页面，不代表已验证任何实际数据。</p>
    <h3>数据情况</h3>
    <p>合成示例：共40条年度记录，训练 {trainCount} 条 / 验证8条 / 测试8条。指标为示例产业规模指数，无实际业务含义。</p>
    <fieldset><legend>是否使用 WGAN 扩充训练数据？</legend>
      <label><input type="radio" name="use-gan" checked={choice.useGan} onChange={() => onChange({ ...choice, useGan: true })} /> 尝试 WGAN：生成后先看 t-SNE，再决定是否采用。</label>
      <label><input type="radio" name="use-gan" checked={!choice.useGan} onChange={() => onChange({ ...choice, useGan: false })} /> 不使用 WGAN：后续直接使用真实数据建模。</label>
    </fieldset>
    {choice.useGan && <><fieldset><legend>训练框架</legend>
      <label><input type="radio" name="framework" checked={choice.framework === "pytorch"} onChange={() => onChange({ ...choice, framework: "pytorch" })} /> PyTorch</label>
      <label><input type="radio" name="framework" checked={choice.framework === "tensorflow"} onChange={() => onChange({ ...choice, framework: "tensorflow" })} /> TensorFlow</label>
    </fieldset><p><strong>生成数量 / 真实训练数量：固定 5 倍。</strong></p>
      <p role="status">{trainCount} 条真实训练数据 × {choice.ratio} = 计划生成 {generated} 条；合计 {trainCount + generated} 条（演示）。</p>
      <p className="muted">训练轮数与生成量分开。5000轮仅沿用配置说明；本页不读取或修改项目 JSON。</p></>}
    <div className="notice">本页面仅切换演示阶段，不启动任何训练任务。</div>
    <div className="actions"><button onClick={onConfirm}>{choice.useGan ? "开始 GAN 演示" : "直接进入 XGBoost 演示"}</button><button onClick={onResult}>返回整理结果</button></div>
  </section>;
}
