import { useEffect, useState } from "react";
import { LineChart } from "../../components/Charts";
import { DistrictMap } from "../../components/DistrictMap";
import { TimelineControls, useTimeline } from "../../components/Timeline";
import { Disclaimer, ErrorBox, Panel } from "../../components/common";
import { api, type RunDetail, type SimHour } from "../../lib/api";
import { fmt } from "../../lib/format";
import { assetName, DISTRICT } from "../../lib/reference";

export function SimulationTab({
  run,
  canCancel,
  onChange,
}: {
  run: RunDetail;
  canCancel: boolean;
  onChange: () => void;
}) {
  const [hours, setHours] = useState<SimHour[]>([]);
  const [error, setError] = useState<unknown>(null);
  const total = run.inputs.params.duration_h;
  const tl = useTimeline(hours.length);
  const live = run.status === "simulating";

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const data = await api.simulation(run.id);
        if (!stop) setHours(data.hours);
      } catch (e) {
        if (!stop) setError(e);
      }
    };
    void load();
    if (!live) return () => void (stop = true);
    const t = window.setInterval(load, 1000);
    return () => {
      stop = true;
      window.clearInterval(t);
    };
  }, [run.id, live]);

  // Follow the newest hour while the simulation is running.
  useEffect(() => {
    if (live && hours.length) tl.setHour(hours.length - 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hours.length, live]);

  const h = hours[tl.hour];
  const pct = Math.round((hours.length / total) * 100);
  return (
    <div className="stack">
      <Panel
        title="Simulation of the approved plan"
        actions={
          live && canCancel ? (
            <button
              className="btn-danger"
              onClick={async () => {
                try {
                  await api.cancel(run.id);
                  onChange();
                } catch (e) {
                  setError(e);
                }
              }}
            >
              Cancel simulation
            </button>
          ) : null
        }
      >
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>
            {live ? "Running" : hours.length ? "Recorded" : "Not started"} · {hours.length}/{total} hours
          </span>
          <span className="faint">Same scenario and seed as the baseline · pause controls playback only</span>
        </div>
        <div
          className="progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          aria-label="Simulation progress"
        >
          <div style={{ width: `${pct}%` }} />
        </div>
        <ErrorBox error={error} />
      </Panel>
      {hours.length > 0 && (
        <div className="grid main-side">
          <Panel title={`District at hour ${tl.hour}`}>
            <DistrictMap
              district={DISTRICT}
              hour={tl.hour}
              title={`Approved plan simulation at hour ${tl.hour}`}
              state={{ zones: h?.zones, closed_edges: h?.closed_edges, states: h?.states }}
            />
            <TimelineControls
              hour={tl.hour}
              length={hours.length}
              playing={tl.playing}
              onHour={tl.setHour}
              onPlaying={tl.setPlaying}
            />
          </Panel>
          <div className="stack">
            <Panel title="Event progression">
              <ul className="trace" aria-live="polite">
                {hours
                  .filter((x) => x.started.length || x.completed.length || x.new_failures.length)
                  .map((x) => (
                    <li key={x.hour} style={{ gridTemplateColumns: "48px 1fr" }}>
                      <b>h{x.hour}</b>
                      <span>
                        {x.started.map((a) => (
                          <div key={`s${a}`}>▶ started {a}</div>
                        ))}
                        {x.completed.map((a) => (
                          <div key={`c${a}`}>✓ completed {a}</div>
                        ))}
                        {x.new_failures.map((a) => (
                          <div key={`f${a}`} className="sev-failed">
                            ✕ {assetName(a)} failed
                          </div>
                        ))}
                      </span>
                    </li>
                  ))}
              </ul>
            </Panel>
            <Panel title="Disruption: plan vs no action">
              <LineChart
                title="Disruption by hour, approved plan versus no action"
                yLabel="disruption"
                cursor={tl.hour}
                series={[
                  {
                    label: "No action",
                    values: hours.map((x) => x.baseline_disruption),
                    color: "var(--failed)",
                    dash: "6 4",
                  },
                  { label: "Approved plan", values: hours.map((x) => x.disruption), color: "var(--accent)" },
                ]}
              />
              {h && (
                <p className="faint">
                  Hour {h.hour}: plan {fmt(h.disruption)} vs no action {fmt(h.baseline_disruption)}
                </p>
              )}
              <Disclaimer />
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}
