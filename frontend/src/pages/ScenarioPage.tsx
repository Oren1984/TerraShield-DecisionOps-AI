import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { DistrictMap } from "../components/DistrictMap";
import { Disclaimer, ErrorBox, Loading, ModeBadge, Panel, StatusBadge } from "../components/common";
import {
  api,
  type Readiness,
  type RunSummary,
  type ScenarioDetail,
  type ScenarioParams,
  type SchemaProp,
} from "../lib/api";
import { useAuth } from "../lib/auth";
import { newKey, when } from "../lib/format";
import { DISTRICT, useCatalog } from "../lib/reference";

type Group = "rainfall" | "coastal" | "wind" | "resources";

function hasInvalidNumber(value: unknown): boolean {
  if (typeof value === "number") return !Number.isFinite(value);
  if (value && typeof value === "object") return Object.values(value).some(hasInvalidNumber);
  return false;
}

function NumberField({
  id,
  label,
  value,
  prop,
  step,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  prop?: SchemaProp;
  step?: number;
  disabled: boolean;
  onChange: (v: number) => void;
}) {
  const unit = prop?.unit ? ` (${prop.unit})` : "";
  const range = prop && prop.minimum !== undefined ? `${prop.minimum}–${prop.maximum}` : "";
  return (
    <div className="field">
      <label htmlFor={id}>
        {label}
        {unit}
      </label>
      <input
        id={id}
        type="number"
        value={Number.isFinite(value) ? value : ""}
        min={prop?.minimum}
        max={prop?.maximum}
        step={step ?? "any"}
        disabled={disabled}
        aria-describedby={`${id}-hint`}
        onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
      />
      <div id={`${id}-hint`} className="hint">
        {prop?.description}
        {range && ` · range ${range}`}
      </div>
    </div>
  );
}

const LABELS: Record<string, string> = {
  base_mm_h: "Background",
  peak_mm_h: "Peak (additional)",
  peak_hour: "Peak hour",
  spread_h: "Spread",
  mean_level_m: "Mean level",
  tide_amplitude_m: "Tide amplitude",
  tide_phase_h: "First high tide",
  surge_peak_m: "Surge peak",
  surge_peak_hour: "Surge peak hour",
  surge_spread_h: "Surge spread",
  base_ms: "Background",
  peak_ms: "Peak (additional)",
  gust_factor: "Gust factor",
  crews: "Crews",
  pumps: "Temporary pumps",
  barriers: "Barrier sets",
  generators: "Generators",
  comms_units: "Mobile comms units",
  budget: "Budget",
  mobilization_h: "Earliest start",
};

export function ScenarioPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const catalog = useCatalog();
  const navigate = useNavigate();
  const [sc, setSc] = useState<ScenarioDetail | null>(null);
  const [params, setParams] = useState<ScenarioParams | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const editable = can("scenarios.write");

  useEffect(() => {
    api.scenario(id).then((s) => {
      setSc(s);
      setParams(structuredClone(s.params));
    }, setError);
    api.scenarioRuns(id).then(setRuns, () => undefined);
    api.readiness().then(setReadiness, () => undefined);
  }, [id]);

  const defs = catalog?.scenario_schema.$defs ?? {};
  const propsFor = (group: string): Record<string, SchemaProp> =>
    defs[group.charAt(0).toUpperCase() + group.slice(1)]?.properties ?? {};
  const dirty = useMemo(() => JSON.stringify(params) !== JSON.stringify(sc?.params), [params, sc]);
  const invalid = useMemo(() => hasInvalidNumber(params), [params]);

  if (!sc || !params) return error ? <ErrorBox error={error} /> : <Loading />;

  const set = <G extends Group>(group: G, key: string, v: number) =>
    setParams({ ...params, [group]: { ...params[group], [key]: v } });

  const save = async () => {
    setError(null);
    try {
      const updated = await api.saveScenario(
        sc.id,
        params,
        sc.current_version,
        note || "Edited in scenario setup",
      );
      setSc(updated);
      setParams(structuredClone(updated.params));
      setNote("");
      setSaved(
        `Saved as version ${updated.current_version}. Earlier approvals for this scenario are no longer valid.`,
      );
    } catch (e) {
      setError(e);
    }
  };

  const start = async (mode: "mock" | "local") => {
    try {
      const run = await api.startRun(sc.id, mode, newKey());
      navigate(`/app/runs/${run.id}`);
    } catch (e) {
      setError(e);
    }
  };

  const group = (g: Group, title: string, steps: Record<string, number> = {}) => {
    const props = propsFor(g);
    const values = params[g] as unknown as Record<string, number>;
    return (
      <fieldset>
        <legend>{title}</legend>
        <div className="grid cols-3">
          {Object.keys(values).map((k) => (
            <NumberField
              key={k}
              id={`${g}-${k}`}
              label={LABELS[k] ?? k}
              value={values[k]}
              prop={props[k]}
              step={steps[k]}
              disabled={!editable}
              onChange={(v) => set(g, k, v)}
            />
          ))}
        </div>
        {params.source_labels[g] && <p className="faint">Source: {params.source_labels[g]}</p>}
      </fieldset>
    );
  };

  return (
    <>
      <div className="page-head">
        <div>
          <p className="faint">
            <Link to={`/app?ws=${sc.workspace_id}`}>Workspace</Link> / Scenario setup
          </p>
          <h1>{sc.title}</h1>
          <p>{sc.summary}</p>
          <div className="row faint">
            <span>Version {sc.current_version}</span>
            <span>Seed {sc.seed}</span>
            <span className="mono">input {sc.input_hash.slice(0, 12)}</span>
          </div>
        </div>
        {can("runs.create") && (
          <div className="row">
            <button
              className="btn-primary"
              onClick={() => start("mock")}
              disabled={dirty}
              data-testid="start-mock"
            >
              Run · Mock
            </button>
            <button onClick={() => start("local")} disabled={dirty || !readiness?.local.ready}>
              Run · Local
            </button>
          </div>
        )}
      </div>
      {dirty && <div className="alert warn">Unsaved changes. Save a new version before starting a run.</div>}
      {saved && (
        <div className="alert ok" role="status">
          {saved}
        </div>
      )}
      <ErrorBox error={error} />
      <div className="grid main-side" style={{ marginTop: 12 }}>
        <div className="stack">
          <Panel title="Parameters">
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              <fieldset>
                <legend>Event</legend>
                <div className="grid cols-3">
                  <NumberField
                    id="duration"
                    label="Duration"
                    value={params.duration_h}
                    prop={catalog?.scenario_schema.properties.duration_h}
                    step={1}
                    disabled={!editable}
                    onChange={(v) => setParams({ ...params, duration_h: v })}
                  />
                </div>
              </fieldset>
              {group("rainfall", "Rainfall", { peak_hour: 0.5 })}
              {group("coastal", "Coastal water level", {})}
              {group("wind", "Wind", {})}
              <fieldset>
                <legend>Initial soil saturation (fraction of pore space, 0–1)</legend>
                <div className="grid cols-4">
                  {DISTRICT.zones.map((z) => (
                    <NumberField
                      key={z.id}
                      id={`sat-${z.id}`}
                      label={`${z.name} (${z.id})`}
                      value={params.soil_saturation[z.id] ?? NaN}
                      prop={{
                        minimum: 0,
                        maximum: 1,
                        unit: "fraction",
                        description: "Blank uses the district default",
                      }}
                      step={0.01}
                      disabled={!editable}
                      onChange={(v) => {
                        const next = { ...params.soil_saturation };
                        if (Number.isNaN(v)) delete next[z.id];
                        else next[z.id] = v;
                        setParams({ ...params, soil_saturation: next });
                      }}
                    />
                  ))}
                </div>
              </fieldset>
              {group("resources", "Resources", {
                crews: 1,
                pumps: 1,
                barriers: 1,
                generators: 1,
                comms_units: 1,
                mobilization_h: 1,
                budget: 500,
              })}
              <fieldset>
                <legend>Hard requirements: assets that must stay operational</legend>
                <div className="grid cols-2">
                  {DISTRICT.assets.map((a) => (
                    <label
                      key={a.id}
                      style={{ color: "var(--text)", display: "flex", gap: 6, alignItems: "center" }}
                    >
                      <input
                        type="checkbox"
                        disabled={!editable}
                        checked={params.must_keep_operational.includes(a.id)}
                        onChange={(e) =>
                          setParams({
                            ...params,
                            must_keep_operational: e.target.checked
                              ? [...params.must_keep_operational, a.id]
                              : params.must_keep_operational.filter((x) => x !== a.id),
                          })
                        }
                      />
                      {a.id} · {a.name}
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="field">
                <label htmlFor="notes">
                  Scenario notes (treated as untrusted data; agents never follow instructions in it)
                </label>
                <textarea
                  id="notes"
                  value={params.notes}
                  disabled={!editable}
                  maxLength={4000}
                  onChange={(e) => setParams({ ...params, notes: e.target.value })}
                />
              </div>
              {editable && (
                <div className="row">
                  <div className="field" style={{ flex: 1, minWidth: 220 }}>
                    <label htmlFor="note">Change note</label>
                    <input id="note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
                  </div>
                  <button
                    className="btn-primary"
                    type="submit"
                    disabled={!dirty || invalid}
                    style={{ alignSelf: "flex-end" }}
                  >
                    Save as version {sc.current_version + 1}
                  </button>
                  <button
                    type="button"
                    disabled={!dirty}
                    onClick={() => setParams(structuredClone(sc.params))}
                    style={{ alignSelf: "flex-end" }}
                  >
                    Discard
                  </button>
                </div>
              )}
              {invalid && <p className="error-text">Some numeric fields are empty or invalid.</p>}
            </form>
          </Panel>
        </div>
        <div className="stack">
          <Panel title="District">
            <DistrictMap
              district={DISTRICT}
              title={`${sc.title}: affected assets highlighted`}
              highlight={params.affected_assets}
            />
          </Panel>
          <Panel title="Provenance and assumptions">
            <p>{sc.template.provenance}</p>
            {sc.template.expected_behavior && (
              <p>
                <b>Expected behaviour:</b> {sc.template.expected_behavior}
              </p>
            )}
            <h3>Assumptions</h3>
            <ul>
              {sc.template.assumptions?.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
            <h3>Known constraints</h3>
            <ul>
              {sc.template.known_constraints?.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
            <Disclaimer />
          </Panel>
          <Panel title="Versions">
            <ul className="trace">
              {sc.versions.map((v) => (
                <li key={v.id} style={{ gridTemplateColumns: "40px 1fr" }}>
                  <b>v{v.version}</b>
                  <span>
                    {v.change_note || "—"} <span className="faint">· {when(v.created_at)}</span>
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="Runs of this scenario">
            {runs.length === 0 ? (
              <p className="faint">No runs yet.</p>
            ) : (
              <ul className="trace">
                {runs.slice(0, 8).map((r) => (
                  <li key={r.id} style={{ gridTemplateColumns: "1fr auto" }}>
                    <span className="row">
                      <StatusBadge status={r.status} /> <ModeBadge mode={r.mode} />{" "}
                      <span className="faint">v{r.scenario_version}</span>
                    </span>
                    <Link to={`/app/runs/${r.id}`}>Open</Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
