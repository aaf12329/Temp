/** 布局入口：RootLayout → 全局基础样式与中文文档；无外部字体或后端依赖。 */
import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "产业信息研究平台",
  description: "公开信息整理、可选WGAN增强、XGBoost评估与研究建议；另有独立设计预览。",
};

/** 为所有页面提供语言与文档结构；由 Next.js 调用。 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
