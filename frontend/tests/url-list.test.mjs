/** 输入合同验收：纯 TXT → 地址与错误行；不请求网络、不读取用户文件。 */
import assert from "node:assert/strict";
import test from "node:test";
import { parseUrlList } from "../src/lib/url-list.ts";

test("BOM、注释、空行和 URL 规范化后去重", () => {
  const result = parseUrlList("\uFEFF# 来源清单\r\n\nHTTPS://example.com:443/report\nhttps://example.com/report\nhttps://example.org/data\n");
  assert.deepEqual(result, { urls: ["https://example.com/report", "https://example.org/data"], rejected: [] });
});

test("拒绝非 HTTP、账号密码与错误行，保留正确原始行号", () => {
  const result = parseUrlList("# 示例\nftp://example.org/x\nhttps://name:password@example.org/x\n不是网址\nhttps://example.org/valid\n");
  assert.deepEqual(result.urls, ["https://example.org/valid"]);
  assert.deepEqual(result.rejected.map((row) => row.line), [2, 3, 4]);
  assert.ok(result.rejected.every((row) => !row.reason.includes("password")));
});

test("空文件和仅注释不产生可提交网址", () => {
  assert.deepEqual(parseUrlList("\uFEFF\n# 全部为注释\n"), { urls: [], rejected: [] });
});
