/** 建模前选择：用户选项 → 内存回调；本阶段不加载训练器或执行 XGBoost/GAN。 */
import type { ModelChoice } from "@/types/research";

type Props = { choice: ModelChoice; confirmed: boolean; onChange: (choice: ModelChoice) => void; onConfirm: () => void; onResult: () => void; onReset: () => void };

/** 提供 WGAN 意向和框架选择，止于训练前确认；由原型入口调用。 */
export function ModelPanel({ choice, confirmed, onChange, onConfirm, onResult, onReset }: Props) {
  return <section className="panel" aria-labelledby="model-title">
    <h2 id="model-title">建模选择（演示）</h2>
    <p>这里演示“真实数据达到条件”的页面，不代表已验证任何实际数据。</p>
    <h3>数据情况</h3>
    <p>指标、地域、统计口径、样本数量、覆盖年份：等待后端实际结果，当前不填造。</p>
    <fieldset><legend>是否使用 WGAN 扩充训练数据？</legend>
      <label><input type="radio" name="use-gan" checked={choice.useGan} onChange={() => onChange({ ...choice, useGan: true })} /> 使用 WGAN：生成的补充样本须通过质量检查和真实验证。</label>
      <label><input type="radio" name="use-gan" checked={!choice.useGan} onChange={() => onChange({ ...choice, useGan: false })} /> 不使用 WGAN：后续直接使用真实数据建模。</label>
    </fieldset>
    {choice.useGan && <><fieldset><legend>训练框架</legend>
      <label><input type="radio" name="framework" checked={choice.framework === "pytorch"} onChange={() => onChange({ ...choice, framework: "pytorch" })} /> PyTorch</label>
      <label><input type="radio" name="framework" checked={choice.framework === "tensorflow"} onChange={() => onChange({ ...choice, framework: "tensorflow" })} /> TensorFlow</label>
    </fieldset><p className="muted">后续训练参数从项目 JSON 读取，沿用 5000 轮配置。本页面尚未读取该文件。</p></>}
    <div className="notice">本阶段到 GAN 训练前为止，确认仅记录当前页面的选择，不会启动训练。</div>
    {confirmed && <p role="status">已记录演示选择：{choice.useGan ? `使用 WGAN / ${choice.framework === "pytorch" ? "PyTorch" : "TensorFlow"}` : "不使用 WGAN"}。刷新页面后清除。</p>}
    <div className="actions"><button onClick={onConfirm}>确认选择（演示）</button><button onClick={onResult}>只查看整理结果</button><button onClick={onReset}>返回首页</button></div>
  </section>;
}
