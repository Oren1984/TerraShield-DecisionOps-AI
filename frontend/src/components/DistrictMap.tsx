import { useId } from "react";
import type { District } from "../lib/api";
import { ASSET_STATE } from "../lib/format";

export interface MapState {
  zones?: Record<string, { depth_m: number; saturation?: number }>;
  closed_edges?: Record<string, string>;
  states?: Record<string, string>;
  active_actions?: string[];
  completed_actions?: string[];
}

export interface Layers {
  water: boolean;
  roads: boolean;
  saturation: boolean;
  labels: boolean;
}

export const DEFAULT_LAYERS: Layers = { water: true, roads: true, saturation: false, labels: true };

interface Props {
  district: District;
  state?: MapState;
  layers?: Layers;
  highlight?: string[];
  restrictions?: Record<string, number>;
  hour?: number;
  title: string;
  animate?: boolean;
  compact?: boolean;
  onSelectAsset?: (id: string) => void;
}

const SHAPE: Record<string, "square" | "diamond" | "circle" | "triangle"> = {
  pump_station: "square",
  substation: "diamond",
  water_works: "square",
  telecom: "triangle",
  clinic: "circle",
  shelter: "circle",
  warehouse: "square",
  residential: "circle",
  depot: "diamond",
};

const CLOSURE_LABEL: Record<string, string> = {
  flood: "flooded",
  ground: "slope closure",
  wind: "wind closure",
  restricted: "ground restriction",
};

function centroid(poly: number[][]): [number, number] {
  const n = poly.length;
  return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
}

function AssetShape({ kind, size }: { kind: string; size: number }) {
  const s = size;
  switch (SHAPE[kind] ?? "circle") {
    case "square":
      return <rect x={-s} y={-s} width={s * 2} height={s * 2} rx={3} />;
    case "diamond":
      return <polygon points={`0,${-s * 1.25} ${s * 1.25},0 0,${s * 1.25} ${-s * 1.25},0`} />;
    case "triangle":
      return <polygon points={`0,${-s * 1.3} ${s * 1.2},${s} ${-s * 1.2},${s}`} />;
    default:
      return <circle r={s} />;
  }
}

export function DistrictMap({
  district,
  state,
  layers = DEFAULT_LAYERS,
  highlight = [],
  restrictions = {},
  hour,
  title,
  animate = false,
  compact = false,
  onSelectAsset,
}: Props) {
  const id = useId();
  const j = Object.fromEntries(district.junctions.map((x) => [x.id, x]));
  const { width, height } = district.canvas;
  const closed = state?.closed_edges ?? {};
  const states = state?.states ?? {};
  const failed = Object.entries(states).filter(([, s]) => s === "failed").length;
  const closedCount = Object.keys(closed).length;
  const descr =
    `Fictional district map${hour !== undefined ? ` at hour ${hour}` : ""}. ` +
    `${failed} asset(s) failed, ${closedCount} road(s) closed.`;

  return (
    <figure className="map-wrap" style={{ margin: 0 }}>
      <svg
        className="map-svg"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-labelledby={`${id}-t`}
        aria-describedby={`${id}-d`}
      >
        <title id={`${id}-t`}>{title}</title>
        <desc id={`${id}-d`}>{descr}</desc>
        <defs>
          <pattern
            id={`${id}-hatch`}
            width="8"
            height="8"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <line x1="0" y1="0" x2="0" y2="8" stroke="var(--water)" strokeWidth="3" opacity="0.5" />
          </pattern>
        </defs>
        <rect width={width} height={height} fill="var(--land)" />
        {district.zones.map((z) => {
          const depth = state?.zones?.[z.id]?.depth_m ?? 0;
          const sat = state?.zones?.[z.id]?.saturation ?? 0;
          const [cx, cy] = centroid(z.polygon);
          const pts = z.polygon.map((p) => p.join(",")).join(" ");
          return (
            <g key={z.id}>
              <polygon points={pts} fill="var(--panel-2)" stroke="var(--line-strong)" strokeWidth={1.5} />
              {layers.saturation && sat > 0 && (
                <polygon
                  points={pts}
                  fill="#c9a46a"
                  opacity={Math.min(0.55, Math.max(0, (sat - 0.4) * 1.1))}
                />
              )}
              {layers.water && depth > 0.02 && (
                <>
                  <polygon
                    points={pts}
                    fill="var(--water)"
                    opacity={Math.min(0.6, 0.12 + depth * 0.5)}
                    className={animate ? "anim-water" : undefined}
                  />
                  {depth > 0.3 && <polygon points={pts} fill={`url(#${id}-hatch)`} />}
                </>
              )}
              {layers.labels && !compact && (
                <text
                  x={cx}
                  y={cy}
                  textAnchor="middle"
                  fontSize={15}
                  fill="var(--text-faint)"
                  fontWeight={600}
                >
                  {z.name}
                  {layers.water && depth > 0.02 ? ` · ${depth.toFixed(2)} m` : ""}
                </text>
              )}
            </g>
          );
        })}
        <polyline
          points={district.river_path.map((p) => p.join(",")).join(" ")}
          fill="none"
          stroke="var(--water)"
          strokeWidth={7}
          opacity={0.55}
          strokeLinecap="round"
        />
        <polygon points={district.sea_polygon.map((p) => p.join(",")).join(" ")} fill="var(--sea)" />
        <g className={animate ? "anim-wave" : undefined} opacity={0.6}>
          {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((i) => (
            <path
              key={i}
              d={`M${i * 100} 618 q 25 -8 50 0 t 50 0`}
              fill="none"
              stroke="var(--water)"
              strokeWidth={2}
            />
          ))}
        </g>
        {layers.roads &&
          district.road_edges.map((e) => {
            const a = j[e.from];
            const b = j[e.to];
            const cause = closed[e.id];
            const restrictedFrom = restrictions[e.id];
            const isRestricted =
              cause === "restricted" || (restrictedFrom !== undefined && hour === undefined);
            const mx = (a.x + b.x) / 2;
            const my = (a.y + b.y) / 2;
            const label = `${e.name} (${e.id})${cause ? `: closed, ${CLOSURE_LABEL[cause] ?? cause}` : ": open"}${
              restrictedFrom !== undefined ? `; restricted from hour ${restrictedFrom}` : ""
            }`;
            return (
              <g key={e.id}>
                <title>{label}</title>
                <line
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={cause ? "var(--failed)" : isRestricted ? "var(--degraded)" : "var(--road)"}
                  strokeWidth={cause ? 4 : 3.5}
                  strokeDasharray={cause || isRestricted ? "9 6" : undefined}
                  strokeLinecap="round"
                />
                {(cause || isRestricted) && (
                  <g transform={`translate(${mx},${my})`}>
                    <circle
                      r={9}
                      fill="var(--bg-elev)"
                      stroke={cause ? "var(--failed)" : "var(--degraded)"}
                    />
                    <text textAnchor="middle" dy="4" fontSize={11} fontWeight={700} fill="var(--text)">
                      {cause ? "✕" : "R"}
                    </text>
                  </g>
                )}
                {layers.labels && !compact && !cause && !isRestricted && (
                  <text x={mx + 6} y={my - 6} fontSize={10} fill="var(--text-faint)">
                    {e.id}
                  </text>
                )}
              </g>
            );
          })}
        {district.junctions.map((x) => (
          <circle
            key={x.id}
            cx={x.x}
            cy={x.y}
            r={x.id === district.depot_junction ? 6 : 3}
            fill="var(--road)"
          />
        ))}
        {district.assets.map((a) => {
          const st = (states[a.id] as keyof typeof ASSET_STATE) ?? "operational";
          const meta = ASSET_STATE[st] ?? ASSET_STATE.operational;
          const hl = highlight.includes(a.id);
          const color =
            st === "failed"
              ? "var(--failed)"
              : st === "unpowered"
                ? "var(--unpowered)"
                : st === "degraded"
                  ? "var(--degraded)"
                  : "var(--ok)";
          const interactive = !!onSelectAsset;
          return (
            <g
              key={a.id}
              transform={`translate(${a.x},${a.y})`}
              onClick={interactive ? () => onSelectAsset(a.id) : undefined}
              style={{ cursor: interactive ? "pointer" : undefined }}
            >
              <title>{`${a.name} (${a.id}): ${meta.label}`}</title>
              {hl && (
                <circle r={18} fill="none" stroke="var(--focus)" strokeWidth={2} strokeDasharray="4 3" />
              )}
              <g fill="var(--bg-elev)" stroke={color} strokeWidth={2.5}>
                <AssetShape kind={a.type} size={10} />
              </g>
              <text textAnchor="middle" dy="4" fontSize={11} fontWeight={700} fill={color}>
                {meta.icon}
              </text>
              {layers.labels && (
                <text x={14} y={-10} fontSize={11} fill="var(--text)" fontWeight={600}>
                  {a.id}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {!compact && (
        <figcaption className="legend">
          <span>
            <b className="sev-ok">✓</b> operational
          </span>
          <span>
            <b className="sev-degraded">!</b> degraded
          </span>
          <span>
            <b className="sev-unpowered">ϟ</b> no power
          </span>
          <span>
            <b className="sev-failed">✕</b> failed
          </span>
          <span>
            <b className="sev-failed">- -✕</b> road closed
          </span>
          <span>
            <b className="sev-degraded">- -R</b> ground restriction
          </span>
          <span>▦ water depth &gt; 0.3 m</span>
          <span className="faint">
            Fictional district; shapes: ■ pumps/works ◆ power/depot ▲ telecom ● buildings
          </span>
        </figcaption>
      )}
      <div className="sr-only">
        <table>
          <caption>Asset states{hour !== undefined ? ` at hour ${hour}` : ""}</caption>
          <tbody>
            {district.assets.map((a) => (
              <tr key={a.id}>
                <th scope="row">{a.name}</th>
                <td>{ASSET_STATE[(states[a.id] as keyof typeof ASSET_STATE) ?? "operational"]?.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
