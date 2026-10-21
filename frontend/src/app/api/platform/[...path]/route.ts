/** HTTP入口：GET/POST → 共用本机代理；动态params按Next合同await。 */
import { proxyPlatform } from "@/server/platform-proxy";
type Context = { params: Promise<{ path: string[] }> };
/** 读取操作或下载文件；不在Node执行Python训练。 */
export async function GET(request: Request, context: Context) { return proxyPlatform(request, (await context.params).path); }
/** 提交人工阶段动作；任务由Python持久Worker执行。 */
export async function POST(request: Request, context: Context) { return proxyPlatform(request, (await context.params).path); }
export const runtime = "nodejs";
