/** 浏览器文件 I/O：文本 → 本地下载；不写服务器文件或项目 output。 */

/** 下载当前页面生成的文本并释放临时地址；供入口聚合层调用。 */
export function downloadText(name: string, content: string, type = "text/plain;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
