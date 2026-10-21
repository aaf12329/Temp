"use client";
/** 工作区显式聚合：ResearchWorkspace → 实际研究 / 独立设计预览，两类状态与数据分开。
 * 由页面入口调用；模式切换 → 对应入口组件，不把演示成功作为真实任务结果。
 */
import { useState } from "react";
import { ResearchPrototype } from "./ResearchPrototype";
import { LiveResearch } from "./LiveResearch";

/** 提供开箱即用的实际入口和设计预览入口，不增加登录。 */
export function ResearchWorkspace() {
  const [preview, setPreview] = useState(false);
  return <><nav className="workspace-nav" aria-label="工作模式"><button aria-pressed={!preview} onClick={() => setPreview(false)}>实际研究</button><button aria-pressed={preview} onClick={() => setPreview(true)}>设计预览（合成演示）</button></nav><div hidden={preview}><main><LiveResearch /></main></div><div hidden={!preview}><ResearchPrototype /></div></>;
}
