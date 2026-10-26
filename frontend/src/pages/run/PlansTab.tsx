import { useState } from "react";
import { Gantt, LineChart, RangeBar, type Series } from "../../components/Charts";
import { Disclaimer, Panel, PassFail } from "../../components/common";
import type { Plan, RunDetail } from "../../lib/api";
import { CONSTRAINT_LABEL, fmt, money } from "../../lib/format";
import { useCatalog } from "../../lib/reference";

const COLORS = ["var(--failed)", "var(--accent)", "var(--water)", "var(--degraded)", "var(--unpowered)"];

export function planStatusBadge(p: Plan) {
  if (p.status === "verified") return <span className="badge ok">✓ verified</span>;
  if (p.status === "infeasible") return <span className="badge bad">✕ infeasible</span>;
  return <span className="badge bad">✕ rejected by verifier</span>;
}

export function PlansTab({ run }: { run: RunDetail }) {
  const plans = run.plans.filter((p) => p.revision === run.revision);
  const older = run.plans.filter((p) => p.revision < run.revision);
  const catalog = useCatalog();
  const best = plans.find((p) => p.rank === 1);
  const [selected, setSelected] = useState<string | null>(null);
  const sel = plans.find((p) => p.id === (selected ?? best?.id ?? plans[0]?.id));
  const maxD = Math.max(
    1,
    ...plans.map((p) => p.sensitivity?.disruption_p90 ?? p.objective.disruption_total ?? 0),
  );
  const series: Series[] = plans.map((p, i) => ({
    label: p.title,
    values: p.outcome.disruption_by_hour,
    color: p.label === "baseline" ? "var(--failed)" : COLORS[(i % (COLORS.length - 1)) + 1],
    dash: p.label === "baseline" ? "6 4" : p.status !== "verified" ? "2 3" : undefined,
  }));
  const exp = run.evidence?.explanation;

  if (!plans.length) {
    return <p className="empty">Plans appear after the planning step.</p>;
  }

  return (
    <div className="stack">
      {run.status === "blocked" && run.blocked_reason && (
        <div className="alert error" role="alert" data-testid="blocked-reason">
          <b>Blocked.</b> {run.blocked_reason}
        </div>
      )}
      {exp && (
        <Panel title="Coordinator explanation">
          <p>{exp.explanation}</p>
          {exp.tradeoffs.length > 0 && (
            <ul>
              {exp.tradeoffs.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          )}
          <p className="faint">
            Recommended: <b>{exp.recommended_plan}</b> · evidence{" "}
            {exp.evidence_ids.map((e) => (
              <span className="chip" key={e}>
                {e}
              </span>
            ))}{" "}
            · citations{" "}
            {exp.citations.length
              ? exp.citations.map((c) => (
                  <span className="chip" key={c}>
                    {c}
                  </span>
                ))
              : "none"}
          </p>
          <p className="faint">
            The ranking is computed by the documented objective, not by the coordinator:{" "}
            {String(catalog?.ranking.description ?? "")}
          </p>
        </Panel>
      )}
      <Panel title="Plan comparison" actions={<span className="faint">revision {run.revision}</span>}>
        <div className="table-wrap">
          <table data-testid="plan-table">
            <thead>
              <tr>
                <th>Plan</th>
                <th>Verification</th>
                <th className="num">Disruption</th>
                <th>Sensitivity (p10–p90)</th>
                <th className="num">Cost</th>
                <th className="num">Done by</th>
                <th className="num">Score</th>
                <th className="num">Rank</th>
              </tr>
            </thead>
            <tbody>
              {plans.map((p) => (
                <tr key={p.id} className={sel?.id === p.id ? "selected" : undefined}>
                  <td>
                    <button
                      className="btn-ghost"
                      style={{ padding: 0, minHeight: 0 }}
                      onClick={() => setSelected(p.id)}
                      aria-pressed={sel?.id === p.id}
                    >
                      <b>{p.title}</b>
                    </button>
                    <div className="faint">
                      {p.method} · {p.solver_status.replace("_", " ")} · {p.actions.length} action(s)
                    </div>
                  </td>
                  <td>{planStatusBadge(p)}</td>
                  <td className="num">{fmt(p.objective.disruption_total)}</td>
                  <td>
                    {p.sensitivity ? (
                      <>
                        <RangeBar
                          p10={p.sensitivity.disruption_p10}
                          p50={p.sensitivity.disruption_p50}
                          p90={p.sensitivity.disruption_p90}
                          max={maxD}
                        />
                        <span className="faint">
                          {fmt(p.sensitivity.disruption_p10, 0)}–{fmt(p.sensitivity.disruption_p90, 0)}
                        </span>
                      </>
                    ) : (
                      "–"
                    )}
                  </td>
                  <td className="num">{money(p.objective.cost)}</td>
                  <td className="num">{p.objective.completion_h ? `h${p.objective.completion_h}` : "–"}</td>
                  <td className="num">{fmt(p.objective.score)}</td>
                  <td className="num">{p.rank ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="faint" style={{ marginTop: 8 }}>
          Disruption is a criticality-weighted asset-hour indicator (lower is better). Sensitivity ranges come
          from seeded perturbations of rainfall, surge, wind and saturation; they are not probabilities.
        </p>
      </Panel>
      <div className="grid cols-2">
        <Panel title="Simulated disruption by hour">
          <LineChart series={series} title="Disruption by hour for each candidate plan" yLabel="disruption" />
          <Disclaimer />
        </Panel>
        {sel && (
          <Panel title={sel.title} actions={planStatusBadge(sel)}>
            <Gantt
              actions={sel.actions}
              horizon={run.inputs.params.duration_h}
              title={`Schedule for ${sel.title}`}
            />
            <h3 style={{ marginTop: 12 }}>Constraint checks (independent verifier)</h3>
            <div className="row" data-testid="checks">
              {sel.checks.map((c) => (
                <PassFail
                  key={c.constraint}
                  passed={c.passed}
                  label={CONSTRAINT_LABEL[c.constraint] ?? c.constraint}
                />
              ))}
            </div>
            {sel.checks
              .filter((c) => !c.passed)
              .map((c) => (
                <details key={c.constraint} open>
                  <summary>Why {CONSTRAINT_LABEL[c.constraint] ?? c.constraint} failed</summary>
                  <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: "0.78rem" }}>
                    {JSON.stringify(c.detail, null, 1)}
                  </pre>
                </details>
              ))}
            {sel.notes.length > 0 && (
              <ul className="faint">
                {sel.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            )}
            <p className="faint mono">plan hash {sel.plan_hash.slice(0, 16)}</p>
          </Panel>
        )}
      </div>
      {older.length > 0 && (
        <details>
          <summary>Earlier revisions ({older.length} plans)</summary>
          <ul>
            {older.map((p) => (
              <li key={p.id}>
                rev {p.revision} · {p.title} · {p.status} · disruption {fmt(p.objective.disruption_total)} ·
                cost {money(p.objective.cost)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
