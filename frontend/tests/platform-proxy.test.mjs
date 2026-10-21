/** 转发边界回归：Node test → 白名单/来源校验；假fetch，不读取真实.env或连接服务。 */
import test from "node:test";
import assert from "node:assert/strict";
import { allowedRoute, proxyPlatform } from "../src/server/platform-proxy.ts";

test("只允许管线提交、状态和登记产物路径", () => {
  assert.ok(allowedRoute("POST", ["pipeline", "jobs"]));
  assert.ok(allowedRoute("GET", ["operations", "run-1"]));
  assert.ok(allowedRoute("GET", ["pipeline", "run-1", "files", "series", "abc123", "gan_tsne.png"]));
  for (const path of [["pipeline", "run", "files", "..", ".env"], ["pipeline", "run", "files", "x\\.env"], ["pipeline", "run", "files", "%2f.env"], ["admin"], ["operations", "../secret"]]) assert.equal(allowedRoute("GET", path), false);
  assert.equal(allowedRoute("DELETE", ["pipeline", "jobs"]), false);
});

test("跨来源提交在读取配置与发请求之前被拒绝", async () => {
  const response = await proxyPlatform(new Request("http://127.0.0.1:3000/api/platform/pipeline/jobs", { method: "POST", headers: { origin: "https://external.invalid" } }), ["pipeline", "jobs"]);
  assert.equal(response.status, 403);
});
