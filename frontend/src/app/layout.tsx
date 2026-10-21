/** 布局入口：RootLayout → 全局基础样式与中文文档；无外部字体或后端依赖。 */
import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "产业信息研究 · 页面原型",
  description: "独立前端结构演示，范围到 GAN 训练之前。",
};

/** 为所有页面提供语言与文档结构；由 Next.js 调用。 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
