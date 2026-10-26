import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { DistrictMap } from "../../components/DistrictMap";
import { Disclaimer, ErrorBox, Panel } from "../../components/common";
import { api, type RunDetail } from "../../lib/api";
import { fmt, newKey, when } from "../../lib/format";
import { assetName, DISTRICT } from "../../lib/reference";

const RETRYABLE = ["failed", "cancelled", "blocked", "rejected"];

export function CompletionTab({
  run,
  onReplay,
  canCreate,
  canLibrary,
}: {
  run: RunDetail;
  onReplay: () => void;
  canCreate: boolean;
  canLibrary: boolean;
}) {
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const o = run.outcome;
  const base = run.baseline?.hours[run.baseline.hours.length - 1];
  const approval = run.approvals.find((a) => a.decision === "approved");
  const plan = run.plans.find((p) => p.id === approval?.plan_id);

  const newRun = async (mode: "mock" | "local", retry = false) => {
    try {
      const r = retry ? await api.retry(run.id, mode) : await api.startRun(run.scenario_id, mode, newKey());
      navigate(`/app/runs/${r.id}`);
    } catch (e) {
      setError(e);
    }
  };

  const actions = (
    <div className="row">
      {run.status === "completed" && <button onClick={onReplay}>Replay simulation</button>}
      {canCreate && (
        <button
          className={run.status === "completed" ? "" : "btn-primary"}
          onClick={() => newRun("mock", RETRYABLE.includes(run.status))}
        >
          {run.status === "completed" ? "New Mock run" : "Start a new Mock run"}
        </button>
      )}
      {canCreate && run.status === "failed" && run.mode === "local" && (
        <button onClick={() => newRun("local", true)}>Retry with local model</button>
      )}
      <Link className="btn" to={`/app/scenarios/${run.scenario_id}`}>
        Scenario setup
      </Link>
      <Link className="btn" to={`/app?ws=${run.workspace_id}`}>
        Back to workspace
      </Link>
    </div>
  );

  if (run.status !== "completed") {
    const msg: Record<string, [string, string]> = {
      blocked: [
        "error",
        "Blocked: no plan was eligible, or the approval stopped being valid. Nothing was simulated as approved.",
      ],
      failed: [
        "error",
        "Failed: the run stopped and the failure is recorded below. Results were not substituted.",
      ],
      cancelled: ["warn", "Cancelled on request. Partial results are kept in the trace."],
      rejected: ["warn", "Rejected by the owner. No plan was simulated."],
    };
    const [tone, text] = msg[run.status] ?? [
      "info",
      `This run is ${run.status.replace("_", " ")}. The completion view appears when it finishes.`,
    ];
    return (
      <div className="stack">
        <div className={`alert ${tone}`} role="status" data-testid="outcome-banner">
          <b>{text}</b>
          {run.blocked_reason && <p style={{ margin: "6px 0 0" }}>{run.blocked_reason}</p>}
          {run.failure_code && (
            <p style={{ margin: "6px 0 0" }}>
              Code <code>{run.failure_code}</code>: {run.failure_detail}
            </p>
          )}
        </div>
        <ErrorBox error={error} />
        {actions}
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="alert ok" role="status" data-testid="outcome-banner">
        <b>Completed.</b> The approved plan ran in the simulator against the same scenario and seed as the
        no-action baseline.
      </div>
      {o && (
        <div className="grid cols-4">
          <div className="panel">
            <div className="faint">Disruption, no action</div>
            <div style={{ fontSize: "1.6rem", fontWeight: 700 }}>{fmt(o.comparison.baseline_disruption)}</div>
          </div>
          <div className="panel">
            <div className="faint">Disruption, approved plan</div>
            <div style={{ fontSize: "1.6rem", fontWeight: 700 }}>{fmt(o.comparison.plan_disruption)}</div>
          </div>
          <div className="panel">
            <div className="faint">Reduction</div>
            <div style={{ fontSize: "1.6rem", fontWeight: 700 }} className="sev-ok">
              {fmt(o.comparison.disruption_reduction_pct)}%
            </div>
          </div>
          <div className="panel">
            <div className="faint">Failures avoided</div>
            <div style={{ fontSize: "1.6rem", fontWeight: 700 }}>{o.comparison.failures_avoided.length}</div>
            <div className="faint">{o.comparison.failures_avoided.join(", ") || "none"}</div>
          </div>
        </div>
      )}
      <div className="grid cols-2">
        <Panel title="Before: no action, final hour">
          {base && (
            <DistrictMap
              district={DISTRICT}
              compact
              title="No-action baseline at the final hour"
              state={{
                zones: base.zones,
                closed_edges: base.closed_edges,
                states: Object.fromEntries(Object.entries(base.assets).map(([k, v]) => [k, v.state])),
              }}
            />
          )}
        </Panel>
        <Panel title="After: approved plan, final hour">
          {o && (
            <DistrictMap
              district={DISTRICT}
              compact
              title="Approved plan at the final hour"
              state={{ closed_edges: o.final_hour.closed_edges, states: o.final_hour.states }}
            />
          )}
        </Panel>
      </div>
      <div className="grid main-side">
        <Panel title="Remaining risks">
          {o && o.remaining_risks.length ? (
            <ul data-testid="remaining-risks">
              {o.remaining_risks.map((r, i) => (
                <li key={i}>
                  <b>{r.name}</b>: {r.detail}
                </li>
              ))}
            </ul>
          ) : (
            <p>None identified by the simulator.</p>
          )}
          {o && o.comparison.failures_remaining.length > 0 && (
            <p className="faint">
              Still failing: {o.comparison.failures_remaining.map(assetName).join(", ")}
            </p>
          )}
          <Disclaimer />
        </Panel>
        <Panel title="Decision record">
          <dl className="kv">
            <dt>Plan</dt>
            <dd>{plan?.title ?? "–"}</dd>
            <dt>Approved by</dt>
            <dd>{approval?.decided_by ?? "–"}</dd>
            <dt>When</dt>
            <dd>{when(approval?.decided_at)}</dd>
            <dt>Reason</dt>
            <dd>{approval?.reason ?? "–"}</dd>
            <dt>Scenario</dt>
            <dd>
              v{run.scenario_version}, seed {run.seed}
            </dd>
            <dt>Plan hash</dt>
            <dd className="mono">{approval?.plan_hash?.slice(0, 16)}</dd>
            <dt>Mode / model</dt>
            <dd>
              {run.mode} · <code>{run.model_id}</code>
            </dd>
          </dl>
          <div className="row" style={{ marginTop: 12 }}>
            {run.has_report && (
              <a
                className="btn btn-primary"
                href={`/api/runs/${run.id}/report.md`}
                download
                data-testid="download-report"
              >
                Download report (.md)
              </a>
            )}
            {canLibrary && (
              <button
                onClick={async () => {
                  try {
                    await api.addToLibrary(run.id);
                    setNote("Added to this workspace's evidence library.");
                  } catch (e) {
                    setError(e);
                  }
                }}
              >
                Add to evidence library
              </button>
            )}
          </div>
          {note && <p className="faint">{note}</p>}
        </Panel>
      </div>
      <ErrorBox error={error} />
      {actions}
    </div>
  );
}
