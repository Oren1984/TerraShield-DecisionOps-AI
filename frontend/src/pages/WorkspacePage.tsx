import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ErrorBox, Loading, ModeBadge, Panel, StatusBadge } from "../components/common";
import { api, type Readiness, type RunSummary, type ScenarioSummary, type Workspace } from "../lib/api";
import { useAuth } from "../lib/auth";
import { newKey, when } from "../lib/format";
import { assetName } from "../lib/reference";

export function ReadinessPanel({ readiness }: { readiness: Readiness | null }) {
  if (!readiness) return <Loading what="Checking model readiness" />;
  const l = readiness.local;
  return (
    <dl className="kv" data-testid="readiness">
      <dt>Default mode</dt>
      <dd>
        <ModeBadge mode="mock" /> always available, no model downloads
      </dd>
      <dt>Local model</dt>
      <dd>
        {l.ready === null ? (
          <span className="faint">{l.detail}</span>
        ) : l.ready ? (
          <span className="badge ok">✓ Ready</span>
        ) : (
          <span className="badge warn">! Not ready</span>
        )}
      </dd>
      {l.ready !== null && (
        <>
          <dt>Ollama</dt>
          <dd>{l.reachable ? "reachable" : "not reachable"}</dd>
          <dt>Chat model</dt>
          <dd>
            <code>{l.chat_model}</code> {l.chat_model_present ? "present" : "missing"}
          </dd>
          <dt>Embeddings</dt>
          <dd>
            <code>{l.embed_model}</code> {l.embed_model_present ? "present" : "missing"}
            {l.embedding && ` · ${l.embedding.embedded_chunks}/${l.embedding.active_chunks} chunks embedded`}
          </dd>
        </>
      )}
    </dl>
  );
}

export function WorkspacePage() {
  const { me, can } = useAuth();
  const [params, setParams] = useSearchParams();
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioSummary[] | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const navigate = useNavigate();
  const wsId = params.get("ws") ?? workspaces?.[0]?.id ?? null;

  useEffect(() => {
    api.workspaces().then(setWorkspaces, setError);
    api.readiness().then(setReadiness, () => undefined);
  }, []);

  const load = useCallback(() => {
    if (!wsId) return;
    api.scenarios(wsId).then(setScenarios, setError);
    api.runs(wsId).then(setRuns, setError);
  }, [wsId]);

  useEffect(load, [load]);

  const start = async (sc: ScenarioSummary, mode: "mock" | "local") => {
    setBusy(`${sc.id}:${mode}`);
    setError(null);
    try {
      const run = await api.startRun(sc.id, mode, newKey());
      navigate(`/app/runs/${run.id}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  const ws = workspaces?.find((w) => w.id === wsId);
  const isDemo = me?.role === "demo";
  const localReady = !!readiness?.local.ready;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{ws ? ws.name : "Workspace"}</h1>
          <p>
            {isDemo
              ? "Read-only demo: seeded runs produced by the real workflow in Mock mode. Replay them and inspect the evidence."
              : "Choose a scenario to inspect or edit, or start a run. Mock mode is the default and needs no model downloads."}
          </p>
        </div>
        {workspaces && workspaces.length > 1 && (
          <div className="field" style={{ minWidth: 240 }}>
            <label htmlFor="ws-select">Workspace</label>
            <select id="ws-select" value={wsId ?? ""} onChange={(e) => setParams({ ws: e.target.value })}>
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      <ErrorBox error={error} />
      <div className="grid main-side">
        <div className="stack">
          <Panel title="Scenarios">
            {!scenarios ? (
              <Loading />
            ) : (
              <div className="card-list">
                {scenarios.map((sc) => (
                  <article
                    key={sc.id}
                    className="panel flat scenario-card"
                    data-testid={`scenario-${sc.slug}`}
                  >
                    <h3>{sc.title}</h3>
                    <p>{sc.summary}</p>
                    <div className="row faint">
                      <span>v{sc.current_version}</span>
                      <span>seed {sc.seed}</span>
                    </div>
                    <div>
                      {sc.affected_assets.map((a) => (
                        <span key={a} className="chip" title={assetName(a)}>
                          {a}
                        </span>
                      ))}
                      {sc.must_keep_operational.map((a) => (
                        <span key={`req-${a}`} className="badge warn" title="Hard requirement">
                          must keep {a} operational
                        </span>
                      ))}
                    </div>
                    <div className="row">
                      <Link className="btn" to={`/app/scenarios/${sc.id}`}>
                        {can("scenarios.write") ? "Set up" : "Details"}
                      </Link>
                      {can("runs.create") && (
                        <>
                          <button
                            className="btn-primary"
                            onClick={() => start(sc, "mock")}
                            disabled={busy !== null}
                            data-testid={`run-mock-${sc.slug}`}
                          >
                            Run · Mock
                          </button>
                          <button
                            onClick={() => start(sc, "local")}
                            disabled={busy !== null || !localReady}
                            title={localReady ? "Run with the local Ollama model" : "Local model not ready"}
                          >
                            Run · Local
                          </button>
                        </>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Recent runs" actions={<button onClick={load}>Refresh</button>}>
            {!runs ? (
              <Loading />
            ) : runs.length === 0 ? (
              <p className="empty">No runs yet. Start one from a scenario card.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Scenario</th>
                      <th>Status</th>
                      <th>Mode</th>
                      <th>Started</th>
                      <th>
                        <span className="sr-only">Open</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={r.id}>
                        <td>
                          {r.scenario_title} <span className="faint">v{r.scenario_version}</span>
                          {r.revision > 0 && <span className="faint"> · revision {r.revision}</span>}
                        </td>
                        <td>
                          <StatusBadge status={r.status} />
                        </td>
                        <td>
                          <ModeBadge mode={r.mode} />
                        </td>
                        <td className="faint">{when(r.created_at)}</td>
                        <td>
                          <Link to={`/app/runs/${r.id}`} aria-label={`Open run ${r.scenario_title}`}>
                            Open
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Model readiness">
            <ReadinessPanel readiness={readiness} />
            {!isDemo && readiness && !readiness.local.ready && (
              <p className="faint" style={{ marginTop: 10 }}>
                Local mode is optional. See docs/DEMO_AND_OPERATIONS.md for the model download step.
              </p>
            )}
          </Panel>
          <Panel title="How a run works">
            <ol style={{ paddingLeft: 18, margin: 0 }} className="muted">
              <li>Simulate the no-action baseline and derive evidence.</li>
              <li>Water, air and ground specialists assess it.</li>
              <li>The coordinator resolves conflicts (one revision round at most).</li>
              <li>The planner builds plans; the verifier checks every one.</li>
              <li>The owner approves, rejects or asks for a revision.</li>
              <li>The approved plan is simulated and compared with no action.</li>
            </ol>
          </Panel>
        </div>
      </div>
    </>
  );
}
