/** 页面入口：Home → ResearchPrototype；当前仅渲染本地交互原型。 */
import { ResearchWorkspace } from "@/features/research/ResearchWorkspace";

/** 提供原型主页面；由 App Router 调用。 */
export default function Home() {
  return <ResearchWorkspace />;
}
