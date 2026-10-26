import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { Brand, HelpPanel } from "../components/Layout";
import { DistrictMap } from "../components/DistrictMap";
import { ErrorBox } from "../components/common";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { DISTRICT } from "../lib/reference";

// A calm, static-looking preview: Harbor Flats and Riverside lightly flooded.
const PREVIEW = {
  zones: { Z1: { depth_m: 0.45 }, Z2: { depth_m: 0.2 } },
  closed_edges: { E2: "ground" },
  states: { A1: "degraded", A3: "operational", A8: "degraded" },
};

export function WelcomePage() {
  const [needsOwner, setNeedsOwner] = useState<boolean | null>(null);
  const [help, setHelp] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const { me, setMe } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    api.setupStatus().then((s) => setNeedsOwner(s.needs_owner), setError);
  }, []);

  const demo = async () => {
    try {
      setMe(await api.demo());
      navigate("/app");
    } catch (e) {
      setError(e);
    }
  };

  return (
    <div className="shell" style={{ gridTemplateRows: "auto 1fr" }}>
      <header className="topbar">
        <Brand />
        <span className="spacer" />
        <button onClick={() => setHelp(true)}>Help</button>
      </header>
      <main id="main" className="hero">
        <section>
          <p className="badge info">Simulation proof of concept</p>
          <h1>Decision support for interacting coastal hazards</h1>
          <p className="lead">
            Explore how water, wind and saturated ground interact in a fictional coastal district. Specialist
            agents assess a scenario, a constrained planner proposes response plans, an independent verifier
            checks every plan, and the owner decides.
          </p>
          <div className="hero-actions">
            {me && me.role !== "demo" ? (
              <Link className="btn btn-primary" to="/app">
                Open workspace
              </Link>
            ) : needsOwner ? (
              <Link className="btn btn-primary" to="/setup">
                Set up the owner account
              </Link>
            ) : (
              <Link className="btn btn-primary" to="/login">
                Owner sign in
              </Link>
            )}
            <button onClick={demo}>Explore the demo (read-only)</button>
          </div>
          <ErrorBox error={error} />
          <ul className="feature-list">
            <li>◆ Deterministic hourly simulator with documented, simplified equations</li>
            <li>◆ Water, air and ground specialists plus a coordinator; Mock or local Ollama models</li>
            <li>◆ CP-SAT planning with hard constraints and an independent verifier</li>
            <li>◆ Versioned owner approval, simulated outcome and downloadable decision record</li>
          </ul>
          <p className="faint" style={{ marginTop: 16 }}>
            All values are demonstrative and uncalibrated. This application issues no emergency instructions
            and controls no real infrastructure.
          </p>
        </section>
        <section aria-label="District preview">
          <div className="panel">
            <DistrictMap
              district={DISTRICT}
              state={PREVIEW}
              title="Harbor Vale, a fictional district"
              animate
            />
          </div>
        </section>
      </main>
      <HelpPanel open={help} onClose={() => setHelp(false)} />
    </div>
  );
}

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const { setMe } = useAuth();
  const navigate = useNavigate();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setMe(await api.login(username, password));
      navigate("/app");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main id="main" className="page">
      <form className="panel auth-card stack" onSubmit={submit} aria-labelledby="login-title">
        <Brand />
        <h1 id="login-title">Sign in</h1>
        <div className="field">
          <label htmlFor="u">Username</label>
          <input
            id="u"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="p">Password</label>
          <input
            id="p"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        <ErrorBox error={error} />
        <div className="row">
          <button className="btn-primary" type="submit" disabled={busy}>
            Sign in
          </button>
          <Link to="/">Back</Link>
        </div>
      </form>
    </main>
  );
}

export function SetupPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<unknown>(null);
  const { setMe } = useAuth();
  const navigate = useNavigate();
  const mismatch = confirm.length > 0 && confirm !== password;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) return;
    try {
      setMe(await api.setupOwner(username, password));
      navigate("/app");
    } catch (err) {
      setError(err);
    }
  };
  return (
    <main id="main" className="page">
      <form className="panel auth-card stack" onSubmit={submit} aria-labelledby="setup-title">
        <Brand />
        <h1 id="setup-title">First-time setup</h1>
        <p className="muted">
          Create the local owner account. There is no default password; choose one with at least 12 characters
          that does not contain the username. The owner can add operators later.
        </p>
        <div className="field">
          <label htmlFor="su">Owner username</label>
          <input
            id="su"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={3}
          />
        </div>
        <div className="field">
          <label htmlFor="sp">Password</label>
          <input
            id="sp"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={12}
          />
        </div>
        <div className="field">
          <label htmlFor="sc">Confirm password</label>
          <input
            id="sc"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            required
            aria-invalid={mismatch}
            aria-describedby={mismatch ? "sc-err" : undefined}
          />
          {mismatch && (
            <p id="sc-err" className="error-text">
              Passwords do not match.
            </p>
          )}
        </div>
        <ErrorBox error={error} />
        <button className="btn-primary" type="submit" disabled={mismatch}>
          Create owner and continue
        </button>
      </form>
    </main>
  );
}
