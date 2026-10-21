/** 纯解析：TXT 正文 → HTTP(S) 地址与错误行；被输入组件调用，不请求网址。 */
import type { ParsedUrls } from "@/types/research";

export const MAX_URLS = 100;
export const MAX_FILE_BYTES = 1024 * 1024;

/** 去除 BOM、空行与注释并去重；错误行单独保留给用户核对。 */
export function parseUrlList(text: string): ParsedUrls {
  const urls = new Set<string>();
  const rejected: ParsedUrls["rejected"] = [];
  text.replace(/^\uFEFF/, "").split(/\r?\n/).forEach((line, index) => {
    const value = line.trim();
    if (!value || value.startsWith("#")) return;
    try {
      const url = new URL(value);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
        throw new Error("需要不含账号密码的 HTTP(S) 网址");
      }
      urls.add(url.href);
    } catch {
      rejected.push({ line: index + 1, reason: "需要不含账号密码的完整 HTTP(S) 网址" });
    }
  });
  return { urls: [...urls], rejected };
}
