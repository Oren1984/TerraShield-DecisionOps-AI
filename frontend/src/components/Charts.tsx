import { useId } from "react";
import type { PlanAction } from "../lib/api";

export interface Series {
  label: string;
  values: number[];
  color: string;
  dash?: string;
}

const PAD = { l: 44, r: 12, t: 12, b: 28 };

export function LineChart({
  series,
  title,
  yLabel,
  height = 200,
  cursor,
}: {
  series: Series[];
  title: string;
  yLabel: string;
  height?: number;
  cursor?: number;
}) {
  const id = useId();
  const width = 640;
  const n = Math.max(1, ...series.map((s) => s.values.length));
  const maxY = Math.max(1, ...series.flatMap((s) => s.values)) * 1.1;
  const x = (i: number) => PAD.l + (i / Math.max(1, n - 1)) * (width - PAD.l - PAD.r);
  const y = (v: number) => height - PAD.b - (v / maxY) * (height - PAD.t - PAD.b);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxY);
  const step = Math.max(1, Math.ceil(n / 9));
  return (
    <figure style={{ margin: 0 }}>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-labelledby={`${id}-t`}
        width="100%"
      >
        <title id={`${id}-t`}>{title}</title>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid-line" x1={PAD.l} x2={width - PAD.r} y1={y(t)} y2={y(t)} />
            <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end">
              {t >= 10 ? Math.round(t) : t.toFixed(1)}
            </text>
          </g>
        ))}
        {Array.from({ length: n }, (_, i) => i)
          .filter((i) => i % step === 0)
          .map((i) => (
            <text key={i} x={x(i)} y={height - 8} textAnchor="middle">
              {i}h
            </text>
          ))}
        <text x={12} y={PAD.t + 4} transform={`rotate(-90 12 ${PAD.t + 4})`} textAnchor="end">
          {yLabel}
        </text>
        {cursor !== undefined && (
          <line
            x1={x(cursor)}
            x2={x(cursor)}
            y1={PAD.t}
            y2={height - PAD.b}
            stroke="var(--focus)"
            strokeWidth={1.5}
          />
        )}
        {series.map((s) => (
          <polyline
            key={s.label}
            fill="none"
            stroke={s.color}
            strokeWidth={2.4}
            strokeDasharray={s.dash}
            points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
          />
        ))}
      </svg>
      <figcaption className="legend">
        {series.map((s) => (
          <span key={s.label}>
            <svg width="22" height="8" aria-hidden="true">
              <line
                x1="0"
                y1="4"
                x2="22"
                y2="4"
                stroke={s.color}
                strokeWidth="2.4"
                strokeDasharray={s.dash}
              />
            </svg>
            {s.label}
          </span>
        ))}
      </figcaption>
      <div className="sr-only">
        <table>
          <caption>{title}</caption>
          <thead>
            <tr>
              <th>Hour</th>
              {series.map((s) => (
                <th key={s.label}>{s.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: n }, (_, i) => (
              <tr key={i}>
                <td>{i}</td>
                {series.map((s) => (
                  <td key={s.label}>{s.values[i]?.toFixed(1)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

export function Gantt({
  actions,
  horizon,
  title,
}: {
  actions: PlanAction[];
  horizon: number;
  title: string;
}) {
  const id = useId();
  const row = 22;
  const width = 640;
  const labelW = 200;
  const height = Math.max(1, actions.length) * row + 24;
  const x = (h: number) => labelW + (h / horizon) * (width - labelW - 10);
  if (!actions.length) return <p className="faint">No actions (no-action plan).</p>;
  return (
    <svg
      className="chart"
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      role="img"
      aria-labelledby={`${id}-t`}
    >
      <title id={`${id}-t`}>{title}</title>
      {Array.from({ length: horizon + 1 }, (_, h) => h)
        .filter((h) => h % 2 === 0)
        .map((h) => (
          <g key={h}>
            <line className="grid-line" x1={x(h)} x2={x(h)} y1={0} y2={height - 18} />
            <text x={x(h)} y={height - 4} textAnchor="middle">
              {h}h
            </text>
          </g>
        ))}
      {actions.map((a, i) => (
        <g key={`${a.type}:${a.target}`}>
          <text x={4} y={i * row + 15}>
            {a.label} · {a.target}
          </text>
          <rect
            className="gantt-bar"
            x={x(a.start_h)}
            y={i * row + 4}
            width={x(a.end_h) - x(a.start_h)}
            height={14}
            rx={3}
          >
            <title>{`${a.label} at ${a.target}: hours ${a.start_h}–${a.end_h}`}</title>
          </rect>
        </g>
      ))}
    </svg>
  );
}

export function RangeBar({ p10, p50, p90, max }: { p10: number; p50: number; p90: number; max: number }) {
  const pct = (v: number) => `${Math.min(100, (v / Math.max(max, 1)) * 100)}%`;
  return (
    <div
      className="range-bar"
      role="img"
      aria-label={`Sensitivity range ${p10.toFixed(0)} to ${p90.toFixed(0)}, median ${p50.toFixed(0)}`}
    >
      <span style={{ left: pct(p10), width: `calc(${pct(p90)} - ${pct(p10)})` }} />
      <i style={{ left: pct(p50) }} />
    </div>
  );
}
