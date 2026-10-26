import { useState } from "react";
import { DEFAULT_LAYERS, DistrictMap, type Layers } from "../../components/DistrictMap";
import { TimelineControls, useTimeline } from "../../components/Timeline";
import { AssetState, Disclaimer, Panel } from "../../components/common";
import type { Assessment, Conflict, RunDetail, TraceEvent } from "../../lib/api";
import { fmt, ROLE_LABEL, when } from "../../lib/format";
import { assetName, DISTRICT, edgeName } from "../../lib/reference";

const SEV_CLS: Record<string, string> = { low: "", moderate: "warn", high: "warn", critical: "bad" };

function latest(assessments: Assessment[], role: string, revision: number): Assessment[] {
  return assessments.filter((a) => a.role === role && a.revision <= revision);
}

function AgentCard({ role, rows }: { role: string; rows: Assessment[] }) {
  const last = [...rows].reverse().find((r) => r.step !== "explain") ?? rows[rows.length - 1];
  const explain = rows.find((r) => r.step === "explain" && r.status === "valid");
  if (!last) {
    return (
      <article className={`panel agent-card role-${role}`} data-testid={`agent-${role}`}>
        <h3>{ROLE_LABEL[role]}</h3>
        <p className="faint">Waiting for this role…</p>
      </article>
    );
  }
  const out = (last.output ?? {}) as Record<string, unknown>;
  const findings = (out.findings as { statement: string; severity: string; evidence_ids: string[] }[]) ?? [];
  const proposals =
    (out.proposed_actions as { action_type: string; target: string; rationale: string }[]) ?? [];
  const restrictions = (out.restrictions as { edge_id: string; from_hour: number; reason: string }[]) ?? [];
  const selected = (out.selected_actions as { action_type: string; target: string }[]) ?? [];
  const citations = (out.citations as string[]) ?? [];
  return (
    <article className={`panel agent-card role-${role}`} data-testid={`agent-${role}`}>
      <header className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>{ROLE_LABEL[role]}</h3>
        <span className={`badge ${last.status === "valid" ? "ok" : "bad"}`}>
          {last.status === "valid" ? "✓ validated" : "✕ invalid"}
        </span>
      </header>
      <p className="faint">
        {last.provider === "mock" ? "Mock rules" : "Local model"} · <code>{last.model_id}</code> · {last.step}{" "}
        round {last.round}
        {last.attempts > 1 && ` · ${last.attempts} attempts`} · {last.latency_ms} ms
      </p>
      {typeof out.summary === "string" && <p>{out.summary}</p>}
      {typeof out.invalid_output_excerpt === "string" && (
        <details>
          <summary>Rejected output excerpt</summary>
          <pre className="mono" style={{ whiteSpace: "pre-wrap" }}>
            {out.invalid_output_excerpt}
          </pre>
        </details>
      )}
      {last.validation_errors.length > 0 && (
        <div className="alert error">
          <b>Validation errors</b>
          <ul>
            {last.validation_errors.slice(0, 6).map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {findings.length > 0 && (
        <details open={role !== "coordinator"}>
          <summary>Findings ({findings.length})</summary>
          <ul>
            {findings.map((f, i) => (
              <li key={i}>
                <span className={`badge ${SEV_CLS[f.severity] ?? ""}`}>{f.severity}</span> {f.statement}{" "}
                {f.evidence_ids.map((e) => (
                  <span key={e} className="chip">
                    {e}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </details>
      )}
      {proposals.length > 0 && (
        <details open>
          <summary>Proposed actions ({proposals.length})</summary>
          <ul>
            {proposals.map((p, i) => (
              <li key={i}>
                <b>
                  {p.action_type} → {p.target}
                </b>{" "}
                <span className="muted">{p.rationale}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {restrictions.length > 0 && (
        <details open>
          <summary>Road restrictions ({restrictions.length})</summary>
          <ul>
            {restrictions.map((r) => (
              <li key={r.edge_id}>
                <b>{edgeName(r.edge_id)}</b> ({r.edge_id}) from hour {r.from_hour}
              </li>
            ))}
          </ul>
        </details>
      )}
      {selected.length > 0 && (
        <p className="faint">
          Passed to planner:{" "}
          {selected.map((s) => (
            <span key={`${s.action_type}:${s.target}`} className="chip">
              {s.action_type}:{s.target}
            </span>
          ))}
        </p>
      )}
      {explain && typeof explain.output?.explanation === "string" && (
        <p>
          <b>Plan explanation:</b> {explain.output.explanation}
        </p>
      )}
      <p className="faint">
        Citations:{" "}
        {citations.length ? (
          citations.map((c) => (
            <span key={c} className="chip">
              {c}
            </span>
          ))
        ) : (
          <em>none (insufficient guidance retrieved)</em>
        )}
      </p>
    </article>
  );
}

export function TraceList({ events }: { events: TraceEvent[] }) {
  return (
    <ul className="trace" aria-live="polite" aria-relevant="additions" data-testid="trace">
      {events.map((e) => (
        <li key={e.id}>
          <time dateTime={e.ts}>{new Date(e.ts).toLocaleTimeString("en-GB")}</time>
          <span className="actor">{e.actor}</span>
          <span>{e.message || e.kind}</span>
        </li>
      ))}
    </ul>
  );
}

export function OperationsTab({ run, events }: { run: RunDetail; events: TraceEvent[] }) {
  const hours = run.baseline?.hours ?? [];
  const tl = useTimeline(hours.length);
  const [layers, setLayers] = useState<Layers>(DEFAULT_LAYERS);
  const h = hours[tl.hour];
  const rounds: { round: number; conflicts: Conflict[] }[] =
    run.evidence?.conflict_rounds ??
    (run.evidence?.conflicts ? [{ round: 1, conflicts: run.evidence.conflicts }] : []);
  const total = rounds.reduce((n, r) => n + r.conflicts.length, 0);
  const resolutionsFor = (round: number) =>
    new Map(
      (
        (run.assessments.find(
          (a) =>
            a.role === "coordinator" &&
            a.step === "consolidate" &&
            a.status === "valid" &&
            a.round === round &&
            a.revision === 0,
        )?.output?.resolutions as { conflict_id: string; resolution: string }[]) ?? []
      ).map((r) => [r.conflict_id, r]),
    );
  const restrictions = run.evidence?.restrictions ?? {};
  const roles = ["water", "air", "ground", "coordinator"];

  return (
    <div className="stack">
      <div className="grid main-side">
        <Panel
          title="Baseline (no action)"
          actions={
            <div className="layer-toggles" role="group" aria-label="Map layers">
              {(Object.keys(layers) as (keyof Layers)[]).map((k) => (
                <label key={k}>
                  <input
                    type="checkbox"
                    checked={layers[k]}
                    onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })}
                  />
                  {k === "saturation" ? "soil saturation" : k}
                </label>
              ))}
            </div>
          }
        >
          {hours.length === 0 ? (
            <p className="empty">The baseline appears once the simulator has run.</p>
          ) : (
            <>
              <DistrictMap
                district={DISTRICT}
                hour={tl.hour}
                layers={layers}
                restrictions={restrictions}
                title={`No-action baseline at hour ${tl.hour}`}
                state={{
                  zones: h?.zones,
                  closed_edges: h?.closed_edges,
                  states: Object.fromEntries(Object.entries(h?.assets ?? {}).map(([k, v]) => [k, v.state])),
                }}
              />
              <TimelineControls
                hour={tl.hour}
                length={hours.length}
                playing={tl.playing}
                onHour={tl.setHour}
                onPlaying={tl.setPlaying}
              />
              {h && (
                <div className="hazard-strip" aria-live="off">
                  <span>
                    Rain <b>{fmt(h.rain_mm_h)}</b> mm/h
                  </span>
                  <span>
                    Sea level <b>{fmt(h.sea_level_m, 2)}</b> m
                  </span>
                  <span>
                    Wind <b>{fmt(h.wind_ms)}</b> m/s (gust <b>{fmt(h.gust_ms)}</b>)
                  </span>
                  <span>
                    Disruption <b>{fmt(h.disruption)}</b>
                  </span>
                </div>
              )}
            </>
          )}
        </Panel>
        <Panel title="Asset states">
          {h ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>State h{tl.hour}</th>
                    <th className="num">Water (m)</th>
                  </tr>
                </thead>
                <tbody>
                  {DISTRICT.assets.map((a) => {
                    const s = h.assets[a.id];
                    return (
                      <tr key={a.id}>
                        <td>
                          <b>{a.id}</b> {a.name}
                          {!s?.reachable && <span className="badge warn">no access</span>}
                        </td>
                        <td>
                          <AssetState state={s?.state ?? "operational"} />
                        </td>
                        <td className="num">{fmt(s?.flood_depth_m, 2)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="faint">No simulation yet.</p>
          )}
          <Disclaimer />
        </Panel>
      </div>

      <section aria-labelledby="agents-title">
        <h2 id="agents-title">Specialist team</h2>
        <div className="grid cols-4">
          {roles.map((r) => (
            <AgentCard key={r} role={r} rows={latest(run.assessments, r, run.revision)} />
          ))}
        </div>
      </section>

      <div className="grid cols-2">
        <Panel title={`Detected conflicts (${total})`}>
          {rounds.length === 0 ? (
            <p className="faint">Not evaluated yet.</p>
          ) : (
            <div className="stack" data-testid="conflicts">
              {rounds.map(({ round, conflicts }) => {
                const res = resolutionsFor(round);
                return (
                  <div key={round}>
                    <h3>
                      Round {round}
                      {round > 1 ? " (after revision)" : ""}
                    </h3>
                    {conflicts.length === 0 ? (
                      <p className="faint">No conflicts between specialist proposals.</p>
                    ) : (
                      <ul style={{ paddingLeft: 18, margin: 0 }}>
                        {conflicts.map((c) => {
                          const r = res.get(c.id);
                          return (
                            <li key={c.id}>
                              <b>
                                {c.id} · {c.type.replaceAll("_", " ")}
                              </b>{" "}
                              <span className="faint">({c.roles.join(" vs ")})</span>
                              <div>{c.detail}</div>
                              {r && (
                                <div className="muted">
                                  Coordinator: <b>{r.resolution.replaceAll("_", " ")}</b>
                                </div>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {Object.keys(restrictions).length > 0 && (
            <p className="faint">
              Accepted ground restrictions (hard constraints):{" "}
              {Object.entries(restrictions).map(([e, hr]) => (
                <span className="chip" key={e}>
                  {e} from h{hr}
                </span>
              ))}
            </p>
          )}
        </Panel>
        <Panel
          title="Execution trace"
          actions={<span className="faint">recorded events, not model reasoning</span>}
        >
          {events.length ? <TraceList events={events} /> : <p className="faint">No events yet.</p>}
        </Panel>
      </div>

      <Panel title={`Structured evidence (${run.evidence?.items.length ?? 0})`}>
        <details>
          <summary>Show evidence items derived from the simulation</summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Domain</th>
                  <th>Statement</th>
                </tr>
              </thead>
              <tbody>
                {(run.evidence?.items ?? []).map((e) => (
                  <tr key={e.id}>
                    <td className="mono">{e.id}</td>
                    <td>{e.domain}</td>
                    <td>{e.statement}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
        <p className="faint" style={{ marginTop: 8 }}>
          Run provenance: seed {run.seed} · input <code>{run.input_hash.slice(0, 16)}</code> ·{" "}
          {run.simulator_version} · {run.planner_version} · {run.policy_version} · {run.workflow_version} ·
          model <code>{run.model_id}</code> · embeddings <code>{run.embedding_model_id}</code> · created{" "}
          {when(run.created_at)}
          {run.analysis_seconds !== null && ` · analysis ${fmt(run.analysis_seconds, 2)} s`}
        </p>
        <p className="faint">
          Highlighted assets: {(run.inputs.params.affected_assets ?? []).map(assetName).join(", ")}
        </p>
      </Panel>
    </div>
  );
}
