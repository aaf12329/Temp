"use client";
/** 真实任务消息叶子：LiveResearch注入进度 → 有界滚动消息框。
 * 只展示Worker事件；滚动查看历史时停止自动跟随，不制造采集或训练百分比。
 */
import { useEffect, useRef } from "react";
import type { PipelineProgress } from "@/types/pipeline";

/** 展示逐网址结果和真实阶段，完成或失败后保留，便于查看原因。 */
export function ResearchMessages({ progress }: { progress: PipelineProgress }) {
  const container = useRef<HTMLOListElement>(null);
  const follow = useRef(true);
  const lastId = progress.events.at(-1)?.id;
  useEffect(() => {
    if (container.current && follow.current) container.current.scrollTop = container.current.scrollHeight;
  }, [lastId]);
  return <section className="panel" aria-labelledby="research-messages-title">
    <h2 id="research-messages-title">运行消息</h2>
    <p>{progress.message}</p>
    {progress.total > 0 && <p>已处理 {progress.completed}/{progress.total}，成功 {progress.succeeded}，失败或跳过 {progress.failed}。</p>}
    <ol className="research-messages" ref={container} role="log" aria-live="polite" aria-relevant="additions" aria-label="真实采集和研究过程"
      onScroll={() => { const element = container.current; if (element) follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 40; }}>
      {progress.events.map((event) => <li key={event.id} data-status={event.status}>
        <span>{event.message}</span>
        {event.url && <span className="muted research-message-url">{event.url}</span>}
        {event.reason && event.status !== "ok" && <small>{event.reason}</small>}
      </li>)}
    </ol>
  </section>;
}
