/** 浏览器I/O统一入口：submit/query/artifactUrl → 同源Next接口；不持有服务密钥。 */
import type { PipelineOperation } from "../types/pipeline.ts";

/** 统一解析业务错误；不把网络失败当作演示成功。 */
async function responseJson(response: Response) {
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error?.message || "服务请求失败，请检查本机服务。");
  return payload;
}
/** 持久提交一个人工阶段，operation_id同时作为重复点击/重试的幂等标识。 */
export async function submitStage(payload: Record<string, unknown>): Promise<{ operation_id: string }> {
  return responseJson(await fetch("/api/platform/pipeline/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
}
/** 获取持久操作真实状态。 */
export async function queryOperation(id: string, signal?: AbortSignal): Promise<PipelineOperation> {
  const operation = await responseJson(await fetch(`/api/platform/operations/${encodeURIComponent(id)}`, { cache: "no-store", signal }));
  if (operation.kind !== "pipeline") throw new Error("该编号不是完整研究管线任务，请检查任务编号。");
  return operation;
}
/** 根据服务器文件清单生成同源下载URL，不接受任意磁盘路径。 */
export function artifactUrl(id: string, reference: string): string {
  return `/api/platform/pipeline/${encodeURIComponent(id)}/files/${reference.split("/").map(encodeURIComponent).join("/")}`;
}
