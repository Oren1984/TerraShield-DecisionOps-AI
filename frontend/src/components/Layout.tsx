import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router";
import overview from "../../../docs/PROJECT_OVERVIEW.md?raw";
import { useAuth } from "../lib/auth";
import { extractHelp, Markdown } from "../lib/markdown";

export function HelpPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="help-title"
        tabIndex={-1}
        ref={ref}
      >
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h1 id="help-title" style={{ margin: 0 }}>
            Help
          </h1>
          <button onClick={onClose} aria-label="Close help">
            Close
          </button>
        </div>
        <p className="faint">From docs/PROJECT_OVERVIEW.md</p>
        <Markdown source={extractHelp(overview)} />
      </aside>
    </>
  );
}

export function Brand() {
  return (
    <span className="brand">
      <img src="/favicon.svg" alt="" width={26} height={26} />
      TerraShield <small>DecisionOps AI</small>
    </span>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { me, logout, can } = useAuth();
  const [help, setHelp] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "?" && !["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) setHelp(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <NavLink to="/app" style={{ textDecoration: "none" }} aria-label="TerraShield workspace">
          <Brand />
        </NavLink>
        <nav aria-label="Main">
          <NavLink to="/app" end>
            Workspace
          </NavLink>
          {can("users.manage") && <NavLink to="/app/users">Users</NavLink>}
          {can("ops.view") && <NavLink to="/app/ops">Operations</NavLink>}
        </nav>
        <span className="spacer" />
        {me && (
          <span className="badge" data-testid="whoami">
            {me.role === "demo" ? "Demo viewer (read-only)" : `${me.username} · ${me.role}`}
          </span>
        )}
        <button onClick={() => setHelp(true)} aria-haspopup="dialog" title="Help (?)">
          Help
        </button>
        <button
          onClick={async () => {
            await logout();
            navigate("/");
          }}
        >
          {me?.role === "demo" ? "Exit demo" : "Sign out"}
        </button>
      </header>
      <div className="poc-banner">
        Simulation proof of concept · fictional district · demonstrative values only · no emergency
        instructions
      </div>
      <main id="main" className="page" tabIndex={-1}>
        {children}
      </main>
      <HelpPanel open={help} onClose={() => setHelp(false)} />
    </div>
  );
}
