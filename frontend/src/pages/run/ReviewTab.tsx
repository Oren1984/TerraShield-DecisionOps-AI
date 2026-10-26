import { useMemo, useState } from "react";
import { ErrorBox, Panel } from "../../components/common";
import { api, type Plan, type RunDetail } from "../../lib/api";
import { fmt, money, when } from "../../lib/format";

type Decision = "approved" | "rejected" | "revision_requested";

export function ReviewForm({
  plans,
  candidateActions,
  disabledReason,
  onSubmit,
}: {
  plans: Plan[];
  candidateActions: string[];
  disabledReason: string | null;
  onSubmit: (d: {
    decision: Decision;
    reason: string;
    plan_id?: string;
    excluded_actions?: string[];
    budget_cap?: number | null;
  }) => Promise<void>;
}) {
  const verified = plans.filter((p) => p.status === "verified");
  const [planId, setPlanId] = useState<string>(() => verified.find((p) => p.rank === 1)?.id ?? "");
  const [reason, setReason] = useState("");
  const [excluded, setExcluded] = useState<string[]>([]);
  const [cap, setCap] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const reasonOk = reason.trim().length >= 3;
  const canApprove = !disabledReason && reasonOk && verified.some((p) => p.id === planId);

  const submit = async (decision: Decision) => {
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        decision,
        reason: reason.trim(),
        plan_id: decision === "approved" ? planId : undefined,
        excluded_actions: decision === "revision_requested" ? excluded : undefined,
        budget_cap: decision === "revision_requested" && cap !== "" ? Number(cap) : null,
      });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="stack" onSubmit={(e) => e.preventDefault()} aria-label="Owner review">
      {disabledReason && <div className="alert warn">{disabledReason}</div>}
      <fieldset>
        <legend>Plan to approve</legend>
        {plans.map((p) => {
          const ok = p.status === "verified";
          return (
            <label
              key={p.id}
              style={{ display: "flex", gap: 8, alignItems: "flex-start", color: "var(--text)" }}
            >
              <input
                type="radio"
                name="plan"
                value={p.id}
                checked={planId === p.id}
                disabled={!ok || !!disabledReason}
                onChange={() => setPlanId(p.id)}
              />
              <span>
                <b>{p.title}</b>{" "}
                {ok ? (
                  <span className="badge ok">✓ verified</span>
                ) : (
                  <span className="badge bad">✕ not eligible</span>
                )}
                <span className="faint">
                  {" "}
                  disruption {fmt(p.objective.disruption_total)} · cost {money(p.objective.cost)} · rank{" "}
                  {p.rank ?? "–"}
                </span>
              </span>
            </label>
          );
        })}
        {verified.length === 0 && (
          <p className="faint">No plan passed every verifier check, so none can be approved.</p>
        )}
      </fieldset>
      <div className="field">
        <label htmlFor="reason">Reason (required, recorded in the decision record)</label>
        <textarea
          id="reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={2000}
          disabled={!!disabledReason}
        />
      </div>
      <details>
        <summary>Revision options</summary>
        <div className="grid cols-2" style={{ marginTop: 8 }}>
          <fieldset>
            <legend>Exclude actions</legend>
            {candidateActions.length === 0 && <p className="faint">No candidate actions recorded.</p>}
            {candidateActions.map((k) => (
              <label key={k} style={{ display: "flex", gap: 6, color: "var(--text)" }}>
                <input
                  type="checkbox"
                  checked={excluded.includes(k)}
                  onChange={(e) =>
                    setExcluded(e.target.checked ? [...excluded, k] : excluded.filter((x) => x !== k))
                  }
                />
                <code>{k}</code>
              </label>
            ))}
          </fieldset>
          <div className="field">
            <label htmlFor="cap">Budget cap (currency units, optional)</label>
            <input
              id="cap"
              type="number"
              min={0}
              max={500000}
              step={500}
              value={cap}
              onChange={(e) => setCap(e.target.value)}
            />
          </div>
        </div>
      </details>
      <ErrorBox error={error} />
      <div className="row">
        <button
          className="btn-primary"
          type="button"
          disabled={!canApprove || busy}
          onClick={() => submit("approved")}
        >
          Approve selected plan
        </button>
        <button
          type="button"
          disabled={!!disabledReason || !reasonOk || busy}
          onClick={() => submit("revision_requested")}
        >
          Request revision
        </button>
        <button
          className="btn-danger"
          type="button"
          disabled={!!disabledReason || !reasonOk || busy}
          onClick={() => submit("rejected")}
        >
          Reject
        </button>
      </div>
    </form>
  );
}

export function ReviewTab({
  run,
  canReview,
  canSimulate,
  onChange,
}: {
  run: RunDetail;
  canReview: boolean;
  canSimulate: boolean;
  onChange: () => void;
}) {
  const plans = useMemo(() => run.plans.filter((p) => p.revision === run.revision), [run]);
  const [error, setError] = useState<unknown>(null);
  const active = run.approvals.find((a) => a.decision === "approved" && a.status === "active");
  const approvedPlan = run.plans.find((p) => p.id === active?.plan_id);
  let disabledReason: string | null = null;
  if (run.status !== "awaiting_review")
    disabledReason = `This run is ${run.status.replace("_", " ")}; no review is pending.`;
  else if (run.scenario_changed_since)
    disabledReason =
      "The scenario changed after this run started. Approval is not possible; start a new run on the current version.";

  return (
    <div className="grid main-side">
      <div className="stack">
        {run.status === "approved" && approvedPlan && (
          <Panel title="Approved — simulation is a separate step">
            <p>
              <b>{approvedPlan.title}</b> was approved by {active?.decided_by} at {when(active?.decided_at)}.
              The approval binds scenario version {run.scenario_version} and plan hash{" "}
              <code>{approvedPlan.plan_hash.slice(0, 16)}</code>. Editing the scenario now invalidates it.
            </p>
            <ErrorBox error={error} />
            {canSimulate ? (
              <button
                className="btn-primary"
                onClick={async () => {
                  setError(null);
                  try {
                    await api.simulate(run.id);
                    onChange();
                  } catch (e) {
                    setError(e);
                    onChange();
                  }
                }}
                data-testid="simulate"
              >
                Run approved plan in the simulator
              </button>
            ) : (
              <p className="faint">Only the owner can start the simulation.</p>
            )}
          </Panel>
        )}
        <Panel title="Owner review">
          {canReview ? (
            <ReviewForm
              key={run.revision}
              plans={plans}
              candidateActions={run.evidence?.candidate_actions ?? []}
              disabledReason={disabledReason}
              onSubmit={async (d) => {
                await api.review(run.id, d);
                onChange();
              }}
            />
          ) : (
            <p className="muted">
              Only the owner can approve, reject or request a revision. Operators can prepare scenarios and
              runs.
            </p>
          )}
        </Panel>
      </div>
      <div className="stack">
        <Panel title="What approval binds">
          <dl className="kv">
            <dt>Scenario version</dt>
            <dd>
              v{run.scenario_version}
              {run.scenario_changed_since && (
                <span className="badge warn">current is v{run.scenario_current_version}</span>
              )}
            </dd>
            <dt>Input hash</dt>
            <dd className="mono">{run.input_hash.slice(0, 24)}</dd>
            <dt>Plan revision</dt>
            <dd>{run.revision}</dd>
            <dt>Mode</dt>
            <dd>{run.mode}</dd>
          </dl>
          <p className="faint" style={{ marginTop: 8 }}>
            Approval and simulation are separate, recorded events. Agents cannot approve; only verified plans
            are eligible.
          </p>
        </Panel>
        <Panel title="Decision history">
          {run.approvals.length === 0 ? (
            <p className="faint">No decisions yet.</p>
          ) : (
            <ul className="trace" data-testid="decisions">
              {run.approvals.map((a) => (
                <li key={a.id} style={{ gridTemplateColumns: "1fr" }}>
                  <span>
                    <b>{a.decision.replace("_", " ")}</b> by {a.decided_by} · {when(a.decided_at)} ·{" "}
                    <span className={`badge ${a.status === "invalidated" ? "bad" : ""}`}>{a.status}</span>
                    <div className="muted">“{a.reason}”</div>
                    {a.invalidated_reason && <div className="error-text">{a.invalidated_reason}</div>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
