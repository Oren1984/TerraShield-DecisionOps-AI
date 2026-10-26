import type { RunStatus } from "./api";

export const STATUS_LABEL: Record<RunStatus, string> = {
  draft: "Draft",
  validated: "Validated",
  queued: "Queued",
  assessing: "Specialists assessing",
  planning: "Planning",
  awaiting_review: "Awaiting owner review",
  approved: "Approved",
  simulating: "Simulating",
  completed: "Completed",
  rejected: "Rejected",
  blocked: "Blocked",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const STATUS_TONE: Record<RunStatus, "ok" | "warn" | "bad" | "info" | ""> = {
  draft: "",
  validated: "",
  queued: "info",
  assessing: "info",
  planning: "info",
  awaiting_review: "warn",
  approved: "ok",
  simulating: "info",
  completed: "ok",
  rejected: "",
  blocked: "bad",
  failed: "bad",
  cancelled: "",
};

export const STATUS_ICON: Record<RunStatus, string> = {
  draft: "○",
  validated: "○",
  queued: "◔",
  assessing: "◑",
  planning: "◕",
  awaiting_review: "!",
  approved: "✓",
  simulating: "▶",
  completed: "✓",
  rejected: "✕",
  blocked: "⛔",
  failed: "✕",
  cancelled: "■",
};

export const LIFECYCLE: RunStatus[] = [
  "queued",
  "assessing",
  "planning",
  "awaiting_review",
  "approved",
  "simulating",
  "completed",
];

export const ASSET_STATE = {
  operational: { icon: "✓", label: "Operational", cls: "sev-ok" },
  degraded: { icon: "!", label: "Degraded", cls: "sev-degraded" },
  unpowered: { icon: "ϟ", label: "No power", cls: "sev-unpowered" },
  failed: { icon: "✕", label: "Failed", cls: "sev-failed" },
} as const;

export const ROLE_LABEL: Record<string, string> = {
  water: "Water specialist",
  air: "Air specialist",
  ground: "Ground specialist",
  coordinator: "Coordinator",
};

export const CONSTRAINT_LABEL: Record<string, string> = {
  allowlist: "Allowlisted actions and targets",
  unique_actions: "Each action used once",
  time_window: "Within time window",
  budget: "Within budget",
  crew_capacity: "No double-booked crews",
  equipment: "Equipment inventory",
  mutual_exclusion: "No mutually exclusive actions",
  prerequisites: "Prerequisites completed first",
  wind_limits: "Wind working limits",
  road_access: "Open route at start (own simulation)",
  requirements: "Scenario requirements met",
};

export function fmt(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  return n.toLocaleString("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function money(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  return n.toLocaleString("en-GB", { maximumFractionDigits: 0 });
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
