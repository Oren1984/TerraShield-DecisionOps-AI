import { useEffect, useState } from "react";

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

export function useTimeline(length: number, intervalMs = 700) {
  const [hour, setHour] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!playing || length === 0) return;
    const t = window.setInterval(() => {
      setHour((h) => {
        if (h >= length - 1) {
          setPlaying(false);
          return h;
        }
        return h + 1;
      });
    }, intervalMs);
    return () => window.clearInterval(t);
  }, [playing, length, intervalMs]);
  useEffect(() => {
    if (hour > length - 1) setHour(Math.max(0, length - 1));
  }, [hour, length]);
  return { hour, setHour, playing, setPlaying };
}

export function TimelineControls({
  hour,
  length,
  playing,
  onHour,
  onPlaying,
  label = "Simulation hour",
}: {
  hour: number;
  length: number;
  playing: boolean;
  onHour: (h: number) => void;
  onPlaying: (p: boolean) => void;
  label?: string;
}) {
  const reduced = usePrefersReducedMotion();
  return (
    <div className="timeline">
      <div className="row">
        <button onClick={() => onHour(Math.max(0, hour - 1))} aria-label="Previous hour" disabled={hour <= 0}>
          ◀
        </button>
        <button
          onClick={() => {
            if (!playing && hour >= length - 1) onHour(0);
            onPlaying(!playing);
          }}
          aria-pressed={playing}
          title={reduced ? "Reduced motion: steps one hour at a time" : undefined}
        >
          {playing ? "Pause" : "Play"}
        </button>
        <button
          onClick={() => onHour(Math.min(length - 1, hour + 1))}
          aria-label="Next hour"
          disabled={hour >= length - 1}
        >
          ▶
        </button>
      </div>
      <input
        type="range"
        min={0}
        max={Math.max(0, length - 1)}
        value={hour}
        onChange={(e) => {
          onPlaying(false);
          onHour(Number(e.target.value));
        }}
        aria-label={label}
        aria-valuetext={`Hour ${hour} of ${length - 1}`}
      />
      <span className="mono" aria-live="off">
        h {hour}/{Math.max(0, length - 1)}
      </span>
    </div>
  );
}
