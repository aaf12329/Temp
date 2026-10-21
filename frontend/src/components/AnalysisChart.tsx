/** 共用绘图：AnalysisChart → SVG坐标/图例；BarChart → SVG条形。
 * 功能页调用；数据点 → 响应式图形，不请求资源、不计算模型。
 */
import { useId } from "react";
type Series = { name: string; color: string; points: [number, number][]; line?: boolean };
type Props = { title: string; xLabel: string; yLabel: string; series: Series[]; diagonal?: boolean; zero?: boolean };

/** 绘制数值散点/折线；每图附可读图例、坐标和演示说明。 */
export function AnalysisChart({ title, xLabel, yLabel, series, diagonal, zero }: Props) {
  const id = useId();
  const all = series.flatMap((item) => item.points);
  const xs = all.map(([x]) => x), ys = all.map(([, y]) => y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys, ...(zero ? [0] : [])), maxY = Math.max(...ys, ...(zero ? [0] : []));
  const loX = diagonal ? Math.min(minX, minY) : minX, hiX = diagonal ? Math.max(maxX, maxY) : maxX;
  const loY = diagonal ? loX : minY, hiY = diagonal ? hiX : maxY;
  const px = (x: number) => 66 + (x - loX) / (hiX - loX || 1) * 390;
  const py = (y: number) => 220 - (y - loY) / (hiY - loY || 1) * 170;
  return <figure className="chart"><figcaption>{title}</figcaption>
    <svg viewBox="0 0 510 285" role="img" aria-labelledby={id}>
      <title id={id}>{title}，合成演示数据</title>
      {[0, .5, 1].map((fraction) => <g key={fraction}>
        <line x1="66" x2="456" y1={50 + fraction * 170} y2={50 + fraction * 170} stroke="#ddd" />
        <text x="58" y={54 + fraction * 170} textAnchor="end">{(hiY - fraction * (hiY - loY)).toFixed(1)}</text>
        <text x={66 + fraction * 390} y="240" textAnchor="middle">{(loX + fraction * (hiX - loX)).toFixed(0)}</text>
      </g>)}
      <path d="M66 45 V220 H460" stroke="#555" fill="none" />
      {diagonal && <line x1={px(loX)} y1={py(loY)} x2={px(hiX)} y2={py(hiY)} stroke="#555" strokeDasharray="5 4" />}
      {zero && <line x1="66" x2="456" y1={py(0)} y2={py(0)} stroke="#555" strokeDasharray="5 4" />}
      {series.map((item) => <g key={item.name}>
        {item.line && <polyline points={item.points.map(([x, y]) => `${px(x)},${py(y)}`).join(" ")} fill="none" stroke={item.color} strokeWidth="2" />}
        {item.points.map(([x, y], index) => <circle key={index} cx={px(x)} cy={py(y)} r="4" fill={item.color}><title>{item.name}：{x}, {y}</title></circle>)}
      </g>)}
      <text x="260" y="271" textAnchor="middle">{xLabel}</text><text transform="translate(16 135) rotate(-90)" textAnchor="middle">{yLabel}</text>
      <text x="456" y="25" textAnchor="end" fill="#777">合成演示 · 非实际训练结果</text>
    </svg><ul className="legend">{series.map((item) => <li key={item.name}><span style={{ background: item.color }} />{item.name}</li>)}</ul>
  </figure>;
}

/** 绘制同单位正值指标条形；不同指标分别展示，避免混用量纲。 */
export function BarChart({ title, rows, unit }: { title: string; rows: { label: string; value: number }[]; unit: string }) {
  const id = useId();
  const largest = Math.max(...rows.map((row) => row.value), 1);
  return <figure className="chart"><figcaption>{title}</figcaption><svg viewBox={`0 0 510 ${rows.length * 44 + 65}`} role="img" aria-labelledby={id}>
    <title id={id}>{title}，合成演示数据</title>
    {rows.map((row, index) => <g key={row.label}><text x="150" y={index * 44 + 30} textAnchor="end">{row.label}</text>
      <rect x="162" y={index * 44 + 14} width={row.value / largest * 235} height="23" fill="#4c6683" />
      <text x={172 + row.value / largest * 235} y={index * 44 + 30}>{row.value.toFixed(2)}</text></g>)}
    <text x="255" y={rows.length * 44 + 22} textAnchor="middle">{unit} · 合成演示</text>
  </svg></figure>;
}
