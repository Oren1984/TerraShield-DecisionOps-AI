import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ErrorBox, Lifecycle, Loading, ModeBadge, StatusBadge } from "../components/common";
import { ACTIVE, api, type RunDetail, type RunStatus, type TraceEvent } from "../lib/api";
import { useAuth } from "../lib/auth";
import { CompletionTab } from "./run/CompletionTab";
import { OperationsTab } from "./run/OperationsTab";
import { PlansTab } from "./run/PlansTab";
import { ReviewTab } from "./run/ReviewTab";
import { SimulationTab } from "./run/SimulationTab";

const TERMINAL: RunStatus[] = ["completed", "rejected", "blocked", "failed", "cancelled"];

const TABS = [
  ["operations", "Operations"],
  ["plans", "Plan comparison"],
  ["review", "Owner review"],
  ["simulation", "Simulation"],
  ["completion", "Completion"],
] as const;
type Tab = (typeof TABS)[number][0];

function defaultTab(status: RunStatus): Tab {
  if (status === "awaiting_review" || status === "blocked" || status === "rejected") return "plans";
  if (status === "approved") return "review";
  if (status === "simulating") return "simulation";
  if (status === "completed") return "completion";
  if (status === "failed" || status === "cancelled") return "completion";
  return "operations";
}

export function RunPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [error, setError] = useState<unknown>(null);
  const lastEvent = useRef(0);
  const tab = (params.get("tab") as Tab | null) ?? (run ? defaultTab(run.status) : "operations");

  const load = useCallback(async () => {
    try {
      const [detail, ev] = await Promise.all([api.run(id), api.events(id, lastEvent.current)]);
      setRun(detail);
      if (ev.events.length) {
        lastEvent.current = ev.events[ev.events.length - 1].id;
        setEvents((prev) => [...prev, ...ev.events]);
      }
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, [id]);

  useEffect(() => {
    lastEvent.current = 0;
    setEvents([]);
    setRun(null);
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const active = run ? !TERMINAL.includes(run.status) : true;
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(t);
  }, [active, load]);

  const select = (t: Tab) => setParams({ tab: t }, { replace: true });
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.findIndex(([k]) => k === tab);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      const next = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length][0];
      select(next);
      document.getElementById(`tab-${next}`)?.focus();
      e.preventDefault();
    }
  };

  if (!run) return error ? <ErrorBox error={error} /> : <Loading what="Loading run" />;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="faint">
            <Link to={`/app?ws=${run.workspace_id}`}>Workspace</Link> /{" "}
            <Link to={`/app/scenarios/${run.scenario_id}`}>{run.scenario_title}</Link> / run{" "}
            <span className="mono">{run.id.slice(0, 8)}</span>
          </p>
          <h1>
            {run.scenario_title} <span className="faint">v{run.scenario_version}</span>
          </h1>
          <div className="row">
            <StatusBadge status={run.status} />
            <ModeBadge mode={run.mode} />
            {run.revision > 0 && <span className="badge">revision {run.revision}</span>}
            {run.is_demo_seed && <span className="badge info">seeded demo run</span>}
            {run.cancel_requested && ACTIVE.includes(run.status) && (
              <span className="badge warn">cancelling…</span>
            )}
          </div>
        </div>
        <div className="stack" style={{ alignItems: "flex-end" }}>
          <Lifecycle status={run.status} />
          {can("runs.cancel") &&
            ["queued", "assessing", "planning", "awaiting_review", "approved"].includes(run.status) && (
              <button
                className="btn-danger"
                onClick={async () => {
                  try {
                    await api.cancel(run.id);
                    await load();
                  } catch (e) {
                    setError(e);
                  }
                }}
              >
                Cancel run
              </button>
            )}
        </div>
      </div>
      {run.scenario_changed_since && (
        <div className="alert warn" style={{ marginBottom: 12 }}>
          The scenario has changed since this run started (now v{run.scenario_current_version}). This run
          keeps its original inputs.
        </div>
      )}
      <ErrorBox error={error} />
      <div className="tabs" role="tablist" aria-label="Run views" onKeyDown={onKey}>
        {TABS.map(([k, label]) => (
          <button
            key={k}
            id={`tab-${k}`}
            role="tab"
            aria-selected={tab === k}
            aria-controls={`panel-${k}`}
            tabIndex={tab === k ? 0 : -1}
            onClick={() => select(k)}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "operations" && <OperationsTab run={run} events={events} />}
        {tab === "plans" && <PlansTab run={run} />}
        {tab === "review" && (
          <ReviewTab
            run={run}
            canReview={can("plans.review")}
            canSimulate={can("runs.simulate")}
            onChange={load}
          />
        )}
        {tab === "simulation" && <SimulationTab run={run} canCancel={can("runs.cancel")} onChange={load} />}
        {tab === "completion" && (
          <CompletionTab
            run={run}
            onReplay={() => select("simulation")}
            canCreate={can("runs.create")}
            canLibrary={can("evidence.manage")}
          />
        )}
      </div>
    </>
  );
}
