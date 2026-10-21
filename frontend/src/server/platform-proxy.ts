/** 服务端转发聚合：allowedRoute → proxyPlatform → 本机FastAPI。
 * Next Route Handler调用；浏览器JSON → 固定白名单路由，Bearer始终留在Node进程。
 * 不接受任意目标地址，不转发浏览器凭据，不暴露根.env内容。
 */
import { existsSync } from "node:fs";
import { PLATFORM_ENV } from "./paths.ts";

/** 限定可转发方法与路径；下载也拒绝点路径和编码后的目录分隔符。 */
export function allowedRoute(method: string, segments: string[]): boolean {
  const id = /^[A-Za-z0-9_-]{1,64}$/;
  if (method === "POST") return segments.join("/") === "pipeline/jobs";
  if (method !== "GET") return false;
  if (segments.length === 2 && segments[0] === "operations") return id.test(segments[1]);
  return segments.length >= 4 && segments[0] === "pipeline" && id.test(segments[1]) && segments[2] === "files"
    && segments.slice(3).every((part) => /^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(part) && !part.includes(".."));
}

/** 转发异步任务提交/查询/文件；调用方只收到业务响应，永远收不到服务token。 */
export async function proxyPlatform(request: Request, segments: string[]): Promise<Response> {
  const url = new URL(request.url);
  // Next内部URL可能使用localhost；浏览器来源应与实际Host一致。
  const expectedOrigin = `${url.protocol}//${request.headers.get("host") || url.host}`;
  const incomingHost = new URL(expectedOrigin).hostname;
  if (!["127.0.0.1", "localhost", "[::1]"].includes(incomingHost) || !allowedRoute(request.method, segments))
    return Response.json({ error: { message: "不支持的本机接口。" } }, { status: 404 });
  if (request.method === "POST" && request.headers.get("origin") !== expectedOrigin)
    return Response.json({ error: { message: "请求来源不匹配，请从本机页面提交。" } }, { status: 403 });
  try {
    if (!process.env.INDUSTRY_API_TOKEN && existsSync(PLATFORM_ENV)) process.loadEnvFile(PLATFORM_ENV);
    const token = process.env.INDUSTRY_API_TOKEN;
    const base = new URL(process.env.INDUSTRY_API_URL || "http://127.0.0.1:8000");
    if (!token || !["127.0.0.1", "localhost", "[::1]"].includes(base.hostname) || base.protocol !== "http:")
      return Response.json({ error: { message: "本机服务未配置，请使用项目一键启动入口。" } }, { status: 503 });
    const upstream = new URL(`/v1/${segments.map(encodeURIComponent).join("/")}`, base);
    upstream.searchParams.set("project_id", "local");
    const body = request.method === "POST" ? await request.text() : undefined;
    if (body && Buffer.byteLength(body) > 1024 * 1024)
      return Response.json({ error: { message: "输入过大，请精简网址清单。" } }, { status: 413 });
    const response = await fetch(upstream, { method: request.method, body,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      cache: "no-store", signal: AbortSignal.timeout(15000) });
    const type = response.headers.get("content-type") || "application/json";
    const headers: Record<string, string> = { "Content-Type": type, "Cache-Control": "no-store" };
    const disposition = response.headers.get("content-disposition");
    if (disposition) headers["Content-Disposition"] = type.startsWith("image/") ? "inline" : disposition;
    return new Response(response.body, { status: response.status, headers });
  } catch {
    return Response.json({ error: { message: "无法连接本机后端，请检查API和Worker是否已启动。" } }, { status: 503 });
  }
}
