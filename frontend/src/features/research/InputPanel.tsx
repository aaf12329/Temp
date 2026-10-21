/** 输入功能：浏览器 TXT → 校验结果 → onSubmit；不上传文件、不请求采集。 */
import { useRef, useState } from "react";
import { MAX_FILE_BYTES, MAX_URLS, parseUrlList } from "@/lib/url-list";
import type { ParsedUrls, ResearchInput } from "@/types/research";

type Props = { onSubmit: (input: ResearchInput) => void; onExample: () => void };

/** 显示输入与可修正错误，提交有效的浏览器本地输入；由原型入口调用。 */
export function InputPanel({ onSubmit, onExample }: Props) {
  const [fileName, setFileName] = useState("");
  const [parsed, setParsed] = useState<ParsedUrls>({ urls: [], rejected: [] });
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [reading, setReading] = useState(false);
  const requestId = useRef(0);

  /** 读取并校验所选 TXT；令牌避免旧文件的异步结果覆盖新选择。 */
  async function readFile(file?: File) {
    const id = ++requestId.current;
    setFileName(""); setParsed({ urls: [], rejected: [] }); setError(""); setReading(false);
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".txt")) { setError("请选择 TXT 文件，每行一个网址。"); return; }
    if (file.size > MAX_FILE_BYTES) { setError("文件超过 1 MB，请缩小网址清单后重试。"); return; }
    setReading(true);
    try {
      const result = parseUrlList(await file.text());
      if (id !== requestId.current) return;
      setFileName(file.name); setParsed(result);
      if (!result.urls.length) setError("没有可用网址，请检查完整的 http:// 或 https:// 地址。");
      else if (result.urls.length > MAX_URLS) setError(`最多支持 ${MAX_URLS} 个去重后的网址，请精简文件。`);
    } catch {
      if (id === requestId.current) setError("文件读取失败，请重新选择 UTF-8 编码的 TXT 文件。");
    } finally { if (id === requestId.current) setReading(false); }
  }

  return <section className="panel" aria-labelledby="input-title">
    <h2 id="input-title">提交研究需求</h2>
    <form onSubmit={(event) => {
      event.preventDefault();
      if (reading || error || !parsed.urls.length || !query.trim()) {
        setError("请先选择有效的网址 TXT，并填写研究需求。"); return;
      }
      onSubmit({ fileName, urls: parsed.urls, query: query.trim() });
    }}>
      <label className="field" htmlFor="url-file">网址清单 TXT</label>
      <input id="url-file" type="file" accept=".txt,text/plain" aria-describedby="file-hint" onChange={(event) => void readFile(event.target.files?.[0])} />
      <p id="file-hint" className="muted">每行一个网址，UTF-8 编码，最多 {MAX_URLS} 个。仅在浏览器读取，不上传。</p>
      <button type="button" onClick={onExample}>下载示例 TXT</button>
      <p aria-live="polite">{reading ? "正在读取文件…" : fileName ? `${fileName}：${parsed.urls.length} 个有效网址（已去重）` : "尚未选择文件"}</p>
      {parsed.rejected.length > 0 && <details><summary>{parsed.rejected.length} 行无效，将忽略</summary>
        <ul>{parsed.rejected.slice(0, 10).map((item) => <li key={item.line}>第 {item.line} 行：{item.reason}</li>)}</ul>
        {parsed.rejected.length > 10 && <p>这里只展示前 10 行错误。</p>}
      </details>}
      <label className="field" htmlFor="research-query">你想研究什么？</label>
      <textarea id="research-query" rows={4} maxLength={4000} required placeholder="例如：整理贵阳餐饮业的收入、规模与发展变化" value={query} onChange={(event) => setQuery(event.target.value)} />
      {error && <p role="alert" className="error">{error}</p>}
      <div className="actions"><button type="submit" disabled={reading || !parsed.urls.length || parsed.urls.length > MAX_URLS || !query.trim()}>确认并开始演示</button></div>
    </form>
  </section>;
}
