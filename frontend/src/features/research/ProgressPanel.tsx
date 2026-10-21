/** 进度功能：注入的演示阶段与日志 → 只读展示；下一步由入口推进。 */
import type { ResearchInput } from "@/types/research";

type Props = { input: ResearchInput; phases: string[]; phase: number; onNext: () => void; onCancel: () => void };

/** 展示逐阶段处理位置和占位原因；本组件不执行计时器或后台任务。 */
export function ProgressPanel({ input, phases, phase, onNext, onCancel }: Props) {
  const finished = phase === phases.length - 1;
  return <section className="panel" aria-labelledby="progress-title">
    <h2 id="progress-title">正在整理信息（演示）</h2>
    <p>研究需求：{input.query}</p><p>网址文件：{input.fileName} · {input.urls.length} 个网址</p>
    <p role="status">当前阶段：{phases[phase]}</p>
    <progress value={phase + 1} max={phases.length} aria-label="演示阶段进度" />
    <ol>{phases.map((label, index) => <li key={label}>{index < phase ? "已演示" : index === phase ? "当前" : "待演示"}：{label}</li>)}</ol>
    <h3>处理记录（合成示例）</h3>
    <ul className="log" aria-live="polite">
      <li>演示：读取网址清单并去重；没有向这些网站发出请求。</li>
      {phase >= 1 && <><li>[成功示例] 网站 A：已读取 HTML 正文。</li><li>[跳过示例] 网站 B：robots.txt 禁止采集。</li><li>[失败示例] 网站 C：访问超时，记录原因。</li></>}
      {phase >= 2 && <li>[演示] 根据研究需求进行本地向量匹配。</li>}
      {phase >= 3 && <li>[演示] 抽取数值、清洗并记录缺失与排除原因。</li>}
      {finished && <li>[演示] 信息整理完成，准备进入所选演示分支。</li>}
    </ul>
    <div className="actions"><button onClick={onNext}>{finished ? "查看整理结果（演示）" : "演示下一阶段"}</button><button onClick={onCancel}>返回输入</button></div>
  </section>;
}
