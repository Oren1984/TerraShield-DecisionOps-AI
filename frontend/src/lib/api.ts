// Typed client for the TerraShield API. All authorization happens server-side;
// the UI only mirrors the capabilities the server reports.

export type Role = "owner" | "operator" | "demo";

export interface Me {
  role: Role;
  username: string | null;
  capabilities: string[];
}

export interface Workspace {
  id: string;
  name: string;
  is_demo: boolean;
  scenarios?: number;
  runs?: number;
}

export interface ScenarioSummary {
  id: string;
  workspace_id: string;
  slug: string;
  title: string;
  summary: string;
  current_version: number;
  version_id: string;
  seed: number;
  input_hash: string;
  affected_assets: string[];
  must_keep_operational: string[];
}

export interface ScenarioParams {
  duration_h: number;
  rainfall: { base_mm_h: number; peak_mm_h: number; peak_hour: number; spread_h: number };
  coastal: {
    mean_level_m: number;
    tide_amplitude_m: number;
    tide_phase_h: number;
    surge_peak_m: number;
    surge_peak_hour: number;
    surge_spread_h: number;
  };
  wind: { base_ms: number; peak_ms: number; peak_hour: number; spread_h: number; gust_factor: number };
  soil_saturation: Record<string, number>;
  resources: {
    crews: number;
    pumps: number;
    barriers: number;
    generators: number;
    comms_units: number;
    budget: number;
    mobilization_h: number;
  };
  must_keep_operational: string[];
  affected_assets: string[];
  source_labels: Record<string, string>;
  notes: string;
}

export interface ScenarioDetail extends ScenarioSummary {
  params: ScenarioParams;
  template: {
    provenance?: string;
    assumptions?: string[];
    known_constraints?: string[];
    expected_behavior?: string;
  };
  versions: { version: number; id: string; change_note: string; created_at: string; input_hash: string }[];
}

export type RunStatus =
  | "draft"
  | "validated"
  | "queued"
  | "assessing"
  | "planning"
  | "awaiting_review"
  | "approved"
  | "simulating"
  | "completed"
  | "rejected"
  | "blocked"
  | "failed"
  | "cancelled";

export interface RunSummary {
  id: string;
  workspace_id: string;
  scenario_id: string;
  scenario_title: string;
  scenario_version: number;
  scenario_changed_since: boolean;
  status: RunStatus;
  mode: "mock" | "local";
  seed: number;
  model_id: string | null;
  embedding_model_id: string | null;
  input_hash: string;
  simulator_version: string;
  planner_version: string;
  policy_version: string;
  workflow_version: string;
  revision: number;
  blocked_reason: string | null;
  failure_code: string | null;
  failure_detail: string | null;
  cancel_requested: boolean;
  is_demo_seed: boolean;
  retry_of: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  analysis_seconds: number | null;
}

export interface AssetHour {
  state: "operational" | "degraded" | "unpowered" | "failed";
  flood_depth_m: number;
  reachable: boolean;
  causes: string[];
}

export interface BaselineHour {
  hour: number;
  rain_mm_h: number;
  sea_level_m: number;
  wind_ms: number;
  gust_ms: number;
  disruption: number;
  closed_edges: Record<string, string>;
  zones: Record<string, { depth_m: number; level_m: number; saturation: number }>;
  assets: Record<string, AssetHour>;
  active_actions: string[];
  completed_actions: string[];
}

export interface Check {
  constraint: string;
  passed: boolean;
  detail: Record<string, unknown>;
}

export interface PlanAction {
  type: string;
  target: string;
  start_h: number;
  end_h: number;
  label: string;
}

export interface Plan {
  id: string;
  revision: number;
  label: string;
  title: string;
  method: string;
  solver_status: string;
  status: "verified" | "rejected_by_verifier" | "infeasible";
  rank: number | null;
  actions: PlanAction[];
  objective: { disruption_total: number | null; cost: number; completion_h: number; score: number | null };
  plan_hash: string;
  outcome: { summary: SimSummary | null; disruption_by_hour: number[] };
  sensitivity: {
    samples: number;
    disruption_p10: number;
    disruption_p50: number;
    disruption_p90: number;
    requirements_met_fraction: number | null;
    label: string;
  } | null;
  notes: string[];
  checks: Check[];
}

export interface SimSummary {
  disruption_total: number;
  disruption_peak: number;
  failed_assets: Record<string, { hour: number; cause: string }>;
  final_states: Record<string, string>;
  requirements: Record<string, { satisfied: boolean; first_violation_hour: number | null }>;
  peak_depth_m: Record<string, number>;
  first_closure: Record<string, { hour: number; cause: string }>;
}

export interface Assessment {
  id: string;
  revision: number;
  round: number;
  role: "water" | "air" | "ground" | "coordinator";
  step: string;
  provider: string;
  model_id: string;
  status: "valid" | "invalid";
  output: Record<string, unknown> | null;
  validation_errors: string[];
  retrieved_chunk_ids: string[];
  attempts: number;
  latency_ms: number;
  created_at: string;
}

export interface EvidenceItem {
  id: string;
  domain: string;
  kind: string;
  statement: string;
  data: Record<string, unknown>;
}

export interface Conflict {
  id: string;
  type: string;
  roles: string[];
  detail: string;
  action?: string;
  feasible_starts?: number[];
}

export interface Approval {
  id: string;
  plan_id: string | null;
  decision: "approved" | "rejected" | "revision_requested";
  reason: string;
  status: string;
  invalidated_reason: string | null;
  decided_by: string;
  decided_at: string;
  scenario_version_id: string;
  plan_hash: string | null;
}

export interface Outcome {
  comparison: {
    baseline_disruption: number;
    plan_disruption: number;
    disruption_reduction: number;
    disruption_reduction_pct: number;
    failures_avoided: string[];
    failures_remaining: string[];
    new_failures: string[];
  };
  remaining_risks: { kind: string; name: string; detail: string }[];
  final_hour: { closed_edges: Record<string, string>; states: Record<string, string> };
  plan_disruption_by_hour: number[];
  baseline_disruption_by_hour: number[];
}

export interface RunDetail extends RunSummary {
  inputs: { params: ScenarioParams; title: string; scenario_version: number };
  evidence: {
    items: EvidenceItem[];
    restrictions?: Record<string, number>;
    conflicts?: Conflict[];
    conflict_rounds?: { round: number; conflicts: Conflict[] }[];
    candidate_actions?: string[];
    explanation?: {
      recommended_plan: string;
      explanation: string;
      tradeoffs: string[];
      evidence_ids: string[];
      citations: string[];
    };
  } | null;
  baseline: { summary: SimSummary; hours: BaselineHour[]; hazards: Record<string, number[]> } | null;
  outcome: Outcome | null;
  assessments: Assessment[];
  plans: Plan[];
  approvals: Approval[];
  has_report: boolean;
  scenario_current_version: number | null;
}

export interface TraceEvent {
  id: number;
  ts: string;
  actor: string;
  kind: string;
  message: string;
  payload: Record<string, unknown>;
}

export interface SimHour {
  hour: number;
  disruption: number;
  baseline_disruption: number;
  new_failures: string[];
  started: string[];
  completed: string[];
  closed_edges: Record<string, string>;
  states: Record<string, string>;
  zones: Record<string, { depth_m: number; saturation: number }>;
  rain_mm_h: number;
  sea_level_m: number;
  wind_ms: number;
}

export interface District {
  name: string;
  provenance: string;
  canvas: { width: number; height: number };
  sea_polygon: number[][];
  river_path: number[][];
  depot_junction: string;
  zones: { id: string; name: string; polygon: number[][]; coastal: boolean; base_elev_m: number }[];
  junctions: { id: string; name: string; x: number; y: number }[];
  road_edges: { id: string; name: string; from: string; to: string; slope_susceptibility: number }[];
  assets: {
    id: string;
    name: string;
    type: string;
    x: number;
    y: number;
    zone: string;
    criticality: number;
  }[];
}

export interface CatalogAction {
  type: string;
  label: string;
  target_kind: string;
  crews: number;
  equipment: Record<string, number>;
  duration_h: number;
  cost: number;
  wind_limit_ms: number;
  description: string;
  specialist: string;
  valid_targets: string[];
  prerequisite: string | null;
}

export interface Catalog {
  actions: CatalogAction[];
  scenario_schema: {
    $defs?: Record<string, { properties: Record<string, SchemaProp> }>;
    properties: Record<string, SchemaProp>;
  };
  ranking: Record<string, number | string>;
  versions: Record<string, string>;
}

export interface SchemaProp {
  description?: string;
  minimum?: number;
  maximum?: number;
  unit?: string;
  default?: unknown;
}

export interface Readiness {
  default_mode: string;
  mock: { ready: boolean; model_id: string };
  local: {
    ready: boolean | null;
    reachable?: boolean;
    chat_model?: string;
    embed_model?: string;
    chat_model_present?: boolean;
    embed_model_present?: boolean;
    detail?: string;
    embedding?: { complete: boolean; embedded_chunks: number; active_chunks: number };
  };
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-Requested-With": "terrashield" } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, detail);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  setupStatus: () => request<{ needs_owner: boolean }>("GET", "/api/setup/status"),
  setupOwner: (username: string, password: string) =>
    request<Me>("POST", "/api/setup/owner", { username, password }),
  login: (username: string, password: string) =>
    request<Me>("POST", "/api/auth/login", { username, password }),
  demo: () => request<Me>("POST", "/api/auth/demo"),
  logout: () => request<{ ok: boolean }>("POST", "/api/auth/logout"),
  me: () => request<Me>("GET", "/api/auth/me"),
  workspaces: () => request<Workspace[]>("GET", "/api/workspaces"),
  createWorkspace: (name: string) =>
    request<Workspace>("POST", "/api/workspaces", { name, seed_scenarios: true }),
  scenarios: (ws: string) => request<ScenarioSummary[]>("GET", `/api/workspaces/${ws}/scenarios`),
  scenario: (id: string) => request<ScenarioDetail>("GET", `/api/scenarios/${id}`),
  saveScenario: (id: string, params: ScenarioParams, expected_version: number, change_note: string) =>
    request<ScenarioDetail>("PUT", `/api/scenarios/${id}`, { params, expected_version, change_note }),
  runs: (ws: string) => request<RunSummary[]>("GET", `/api/workspaces/${ws}/runs`),
  scenarioRuns: (id: string) => request<RunSummary[]>("GET", `/api/scenarios/${id}/runs`),
  startRun: (scenarioId: string, mode: "mock" | "local", idempotencyKey: string) =>
    request<RunSummary>(
      "POST",
      `/api/scenarios/${scenarioId}/runs`,
      { mode },
      { "Idempotency-Key": idempotencyKey },
    ),
  run: (id: string) => request<RunDetail>("GET", `/api/runs/${id}`),
  events: (id: string, after: number) =>
    request<{ status: RunStatus; events: TraceEvent[] }>("GET", `/api/runs/${id}/events?after=${after}`),
  simulation: (id: string) =>
    request<{ status: RunStatus; duration_h: number; hours: SimHour[] }>("GET", `/api/runs/${id}/simulation`),
  review: (
    id: string,
    body: {
      decision: "approved" | "rejected" | "revision_requested";
      reason: string;
      plan_id?: string;
      excluded_actions?: string[];
      budget_cap?: number | null;
    },
  ) => request<RunSummary>("POST", `/api/runs/${id}/review`, body),
  simulate: (id: string) => request<RunSummary>("POST", `/api/runs/${id}/simulate`),
  cancel: (id: string) => request<RunSummary & { cancel_result: string }>("POST", `/api/runs/${id}/cancel`),
  retry: (id: string, mode: "mock" | "local") =>
    request<RunSummary>("POST", `/api/runs/${id}/retry`, { mode }),
  addToLibrary: (id: string) => request<{ document_id: string }>("POST", `/api/runs/${id}/library`),
  district: () => request<District>("GET", "/api/district"),
  catalog: () => request<Catalog>("GET", "/api/catalog"),
  readiness: () => request<Readiness>("GET", "/api/models/readiness"),
  ops: () => request<Record<string, unknown>>("GET", "/api/ops/status"),
  users: () =>
    request<{ id: string; username: string; role: string; is_active: boolean; workspace_ids: string[] }[]>(
      "GET",
      "/api/users",
    ),
  createUser: (username: string, password: string, role: string, workspace_ids: string[]) =>
    request("POST", "/api/users", { username, password, role, workspace_ids }),
  patchUser: (id: string, body: { is_active?: boolean; password?: string; workspace_ids?: string[] }) =>
    request("PATCH", `/api/users/${id}`, body),
};

export const ACTIVE: RunStatus[] = ["queued", "assessing", "planning", "simulating"];
