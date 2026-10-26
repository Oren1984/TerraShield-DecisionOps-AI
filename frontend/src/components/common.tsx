import type { ReactNode } from "react";
import type { RunStatus } from "../lib/api";
import { ASSET_STATE, LIFECYCLE, STATUS_ICON, STATUS_LABEL, STATUS_TONE } from "../lib/format";

export function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={`badge ${STATUS_TONE[status]}`} data-testid="run-status" data-status={status}>
      <span aria-hidden="true">{STATUS_ICON[status]}</span>
      {STATUS_LABEL[status]}
    </span>
  );
}

export function ModeBadge({ mode }: { mode: "mock" | "local" }) {
  return (
    <span
      className={`badge ${mode}`}
      title={
        mode === "mock"
          ? "Seeded rule-based agents; no model inference"
          : "Real local model inference (Ollama)"
      }
    >
      {mode === "mock" ? "Mock mode" : "Local model"}
    </span>
  );
}

export function AssetState({ state }: { state: string }) {
  const meta = ASSET_STATE[state as keyof typeof ASSET_STATE] ?? ASSET_STATE.operational;
  return (
    <span className={`badge ${meta.cls}`}>
      <span aria-hidden="true">{meta.icon}</span>
      {meta.label}
    </span>
  );
}

export function PassFail({ passed, label }: { passed: boolean; label: string }) {
  return (
    <span className={`badge ${passed ? "ok" : "bad"}`}>
      <span aria-hidden="true">{passed ? "✓" : "✕"}</span>
      {label}
      <span className="sr-only">{passed ? "passed" : "failed"}</span>
    </span>
  );
}

export function Lifecycle({ status }: { status: RunStatus }) {
  const idx = LIFECYCLE.indexOf(status);
  const terminalOff = idx === -1;
  return (
    <ol className="status-steps" aria-label="Run lifecycle">
      {LIFECYCLE.map((s, i) => (
        <li
          key={s}
          className={!terminalOff && i < idx ? "done" : !terminalOff && i === idx ? "current" : ""}
          aria-current={!terminalOff && i === idx ? "step" : undefined}
        >
          {STATUS_LABEL[s]}
        </li>
      ))}
      {terminalOff && (
        <li className="current" aria-current="step">
          {STATUS_LABEL[status]}
        </li>
      )}
    </ol>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="muted" role="status" aria-live="polite">
      {what}…
    </p>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="alert error" role="alert">
      {msg}
    </div>
  );
}

export function Panel({
  title,
  actions,
  children,
  className = "",
  headingLevel = 2,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  headingLevel?: 2 | 3;
}) {
  const H = headingLevel === 2 ? "h2" : "h3";
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header>
          {title && <H>{title}</H>}
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Disclaimer() {
  return (
    <p className="faint" style={{ margin: 0 }}>
      Simulation POC: values are demonstrative outputs of an uncalibrated model of a fictional district. Not a
      forecast or emergency instruction.
    </p>
  );
}
