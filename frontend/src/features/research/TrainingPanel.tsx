/** 训练进度功能：TrainingPanel → 阶段、日志和动作；聚合入口传入状态。
 * 阶段快照 → 手动演示，不运行计时器、训练器或网络任务。
 */
type Props = { kind: "gan" | "xgboost"; phases: string[]; phase: number; description: string; error?: boolean; onNext: () => void; onFail?: () => void; onRetry?: () => void; onBack: () => void };

/** 展示GAN/XGBoost共享的手动进度与失败退路。 */
export function TrainingPanel({ kind, phases, phase, description, error, onNext, onFail, onRetry, onBack }: Props) {
  const name = kind === "gan" ? "WGAN" : "XGBoost";
  return <section className="panel" aria-labelledby="training-title">
    <h2 id="training-title">{name} 训练过程（演示）</h2><p>{description}</p>
    <p className="notice">当前仅手动切换示例阶段，没有执行任何模型训练。</p>
    <progress value={phase + 1} max={phases.length} aria-label={`${name}演示进度`} />
    <ol>{phases.map((label, index) => <li key={label}>{index < phase ? "已演示" : index === phase ? "当前" : "待演示"}：{label}</li>)}</ol>
    {error ? <div role="alert" className="error"><p>失败示例：训练任务中断。已有整理数据仍可返回查看；没有实际任务需要停止。</p><button onClick={onRetry}>重新演示训练</button></div>
      : <p role="status">演示记录：{phases[phase]}。实际轮数、耗时、设备和异常信息等待后端接入。</p>}
    <div className="actions"><button disabled={error} onClick={onNext}>{phase < phases.length - 1 ? "演示下一阶段" : kind === "gan" ? "查看 t-SNE 与生成数据" : "查看模型评估"}</button>
      {onFail && <button disabled={error} onClick={onFail}>演示训练失败</button>}<button onClick={onBack}>返回建模选择</button></div>
  </section>;
}
