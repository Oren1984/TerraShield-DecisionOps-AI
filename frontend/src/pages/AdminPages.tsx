import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ErrorBox, Loading, Panel } from "../components/common";
import { api, type Workspace } from "../lib/api";

type UserRow = { id: string; username: string; role: string; is_active: boolean; workspace_ids: string[] };

export function UsersPage() {
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [form, setForm] = useState({ username: "", password: "", role: "operator", ws: [] as string[] });
  const [wsName, setWsName] = useState("");
  const load = useCallback(() => {
    api.users().then(setUsers, setError);
    api.workspaces().then(setWorkspaces, setError);
  }, []);
  useEffect(load, [load]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.createUser(form.username, form.password, form.role, form.ws);
      setForm({ username: "", password: "", role: "operator", ws: [] });
      load();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Users and workspaces</h1>
          <p>
            Local accounts only. Operators see only the workspaces assigned to them. Deactivating a user ends
            their sessions.
          </p>
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="grid main-side">
        <Panel title="Accounts">
          {!users ? (
            <Loading />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Role</th>
                    <th>Workspaces</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td>{u.username}</td>
                      <td>{u.role}</td>
                      <td>
                        {u.role === "owner"
                          ? "all"
                          : u.workspace_ids
                              .map((w) => workspaces.find((x) => x.id === w)?.name ?? w)
                              .join(", ") || "none"}
                      </td>
                      <td>
                        <button
                          onClick={async () => {
                            try {
                              await api.patchUser(u.id, { is_active: !u.is_active });
                              load();
                            } catch (err) {
                              setError(err);
                            }
                          }}
                          aria-label={`${u.is_active ? "Deactivate" : "Activate"} ${u.username}`}
                        >
                          {u.is_active ? "Active · deactivate" : "Inactive · activate"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <div className="stack">
          <Panel title="Add a user">
            <form className="stack" onSubmit={create}>
              <div className="field">
                <label htmlFor="nu">Username</label>
                <input
                  id="nu"
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                  required
                  minLength={3}
                />
              </div>
              <div className="field">
                <label htmlFor="np">Initial password (12+ characters)</label>
                <input
                  id="np"
                  type="password"
                  autoComplete="new-password"
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  required
                  minLength={12}
                />
              </div>
              <div className="field">
                <label htmlFor="nr">Role</label>
                <select
                  id="nr"
                  value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                >
                  <option value="operator">Operator</option>
                  <option value="owner">Owner</option>
                </select>
              </div>
              <fieldset>
                <legend>Workspaces</legend>
                {workspaces.map((w) => (
                  <label key={w.id} style={{ display: "flex", gap: 6, color: "var(--text)" }}>
                    <input
                      type="checkbox"
                      checked={form.ws.includes(w.id)}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          ws: e.target.checked ? [...form.ws, w.id] : form.ws.filter((x) => x !== w.id),
                        })
                      }
                    />
                    {w.name}
                  </label>
                ))}
              </fieldset>
              <button className="btn-primary" type="submit">
                Create user
              </button>
            </form>
          </Panel>
          <Panel title="Add a workspace">
            <form
              className="row"
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  await api.createWorkspace(wsName);
                  setWsName("");
                  load();
                } catch (err) {
                  setError(err);
                }
              }}
            >
              <label htmlFor="wn" className="sr-only">
                Workspace name
              </label>
              <input
                id="wn"
                value={wsName}
                onChange={(e) => setWsName(e.target.value)}
                placeholder="Workspace name"
                required
                minLength={3}
                style={{ flex: 1 }}
              />
              <button type="submit">Create</button>
            </form>
            <p className="faint">New workspaces start with the four seeded scenarios.</p>
          </Panel>
        </div>
      </div>
    </>
  );
}

export function OpsPage() {
  const [ops, setOps] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const load = () => api.ops().then(setOps, setError);
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, []);
  const obj = (k: string) => (ops?.[k] ?? {}) as Record<string, unknown>;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Operations</h1>
          <p>
            Queue and run status, observed analysis latency on this machine, validation failures and job
            retries.
          </p>
        </div>
      </div>
      <ErrorBox error={error} />
      {!ops ? (
        <Loading />
      ) : (
        <div className="grid cols-3">
          <Panel title="Jobs">
            <dl className="kv">
              {Object.entries(obj("jobs")).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k}</dt>
                  <dd>{String(v)}</dd>
                </div>
              ))}
              <dt>retries</dt>
              <dd>{String(ops.job_retries)}</dd>
            </dl>
          </Panel>
          <Panel title="Runs">
            <dl className="kv">
              {Object.entries(obj("runs")).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k}</dt>
                  <dd>{String(v)}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title="Model outputs">
            <dl className="kv">
              <dt>validation rejections</dt>
              <dd>{String(ops.validation_rejections)}</dd>
              <dt>invalid outputs</dt>
              <dd>{String(ops.invalid_agent_outputs)}</dd>
            </dl>
          </Panel>
          <Panel title="Analysis latency (measured)">
            <dl className="kv">
              {Object.entries(obj("analysis_latency")).map(([mode, v]) => {
                const x = v as { runs: number; mean_analysis_s: number; max_analysis_s: number };
                return (
                  <div key={mode} style={{ display: "contents" }}>
                    <dt>{mode}</dt>
                    <dd>
                      {x.runs} runs · mean {x.mean_analysis_s}s · max {x.max_analysis_s?.toFixed?.(1)}s
                    </dd>
                  </div>
                );
              })}
            </dl>
          </Panel>
          <Panel title="Recent failures">
            <ul>
              {((ops.recent_failures as { run_id: string; code: string }[]) ?? []).map((f) => (
                <li key={f.run_id}>
                  <code>{f.code}</code> · <a href={`/app/runs/${f.run_id}`}>{f.run_id.slice(0, 8)}</a>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      )}
    </>
  );
}
