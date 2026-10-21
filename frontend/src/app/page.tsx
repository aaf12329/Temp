/** 页面入口：Home → ResearchPrototype；当前仅渲染本地交互原型。 */
import { ResearchPrototype } from "@/features/research/ResearchPrototype";

/** 提供原型主页面；由 App Router 调用。 */
export default function Home() {
  return <ResearchPrototype />;
}
