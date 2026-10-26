import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Plan } from "../lib/api";
import { extractHelp } from "../lib/markdown";
import { DISTRICT } from "../lib/reference";
import { ReviewForm } from "../pages/run/ReviewTab";
import { DistrictMap } from "./DistrictMap";
import { HelpPanel } from "./Layout";
import { TimelineControls } from "./Timeline";
import { Lifecycle, PassFail, StatusBadge } from "./common";

function plan(over: Partial<Plan>): Plan {
  return {
    id: "p1",
    revision: 0,
    label: "cpsat_balanced",
    title: "Constrained plan: balanced",
    method: "cp-sat",
    solver_status: "optimal",
    status: "verified",
    rank: 1,
    actions: [],
    objective: { disruption_total: 50, cost: 10000, completion_h: 4, score: 57 },
    plan_hash: "abc",
    outcome: { summary: null, disruption_by_hour: [] },
    sensitivity: null,
    notes: [],
    checks: [],
    ...over,
  };
}

describe("ReviewForm", () => {
  const plans = [
    plan({}),
    plan({
      id: "p2",
      label: "baseline",
      title: "No action (baseline)",
      status: "rejected_by_verifier",
      rank: null,
    }),
  ];

  it("requires a reason and a verified plan before approval", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ReviewForm plans={plans} candidateActions={[]} disabledReason={null} onSubmit={onSubmit} />);
    const approve = screen.getByRole("button", { name: "Approve selected plan" });
    expect(approve).toBeDisabled();
    expect(screen.getByRole("radio", { name: /No action/ })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Reason/), "Protect the pump station");
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ decision: "approved", plan_id: "p1", reason: "Protect the pump station" }),
    );
  });

  it("sends exclusions and budget cap with a revision request", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <ReviewForm
        plans={plans}
        candidateActions={["sandbag_asset:A1"]}
        disabledReason={null}
        onSubmit={onSubmit}
      />,
    );
    await userEvent.type(screen.getByLabelText(/Reason/), "Cheaper please");
    await userEvent.click(screen.getByText("Revision options"));
    await userEvent.click(screen.getByRole("checkbox", { name: "sandbag_asset:A1" }));
    await userEvent.type(screen.getByLabelText(/Budget cap/), "9000");
    await userEvent.click(screen.getByRole("button", { name: "Request revision" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        decision: "revision_requested",
        excluded_actions: ["sandbag_asset:A1"],
        budget_cap: 9000,
      }),
    );
  });

  it("disables every decision when review is not possible", () => {
    render(
      <ReviewForm plans={plans} candidateActions={[]} disabledReason="Scenario changed" onSubmit={vi.fn()} />,
    );
    expect(screen.getByText("Scenario changed")).toBeInTheDocument();
    for (const name of ["Approve selected plan", "Request revision", "Reject"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
  });
});

describe("DistrictMap", () => {
  it("exposes states without relying on colour", () => {
    render(
      <DistrictMap
        district={DISTRICT}
        title="Test map"
        hour={3}
        state={{ states: { A2: "failed", A3: "unpowered" }, closed_edges: { E2: "ground" } }}
      />,
    );
    const img = screen.getByRole("img", { name: /Test map/ });
    expect(img).toHaveAccessibleDescription(/1 asset\(s\) failed, 1 road\(s\) closed/);
    const table = screen.getByRole("table", { name: /Asset states at hour 3/ });
    expect(within(table).getByRole("row", { name: /Quay Pump Station Failed/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Marsh Lane Substation No power/ })).toBeInTheDocument();
    expect(screen.getByText(/West Descent \(E2\): closed, slope closure/)).toBeInTheDocument();
  });
});

describe("status components", () => {
  it("marks the current lifecycle step and shows icon plus text", () => {
    render(
      <>
        <Lifecycle status="planning" />
        <StatusBadge status="blocked" />
        <PassFail passed={false} label="Within budget" />
      </>,
    );
    expect(screen.getByText("Planning")).toHaveAttribute("aria-current", "step");
    expect(screen.getByTestId("run-status")).toHaveTextContent("Blocked");
    expect(screen.getByText("failed")).toHaveClass("sr-only");
  });
});

describe("TimelineControls", () => {
  it("supports keyboard-free stepping and play toggling", async () => {
    const onHour = vi.fn();
    const onPlaying = vi.fn();
    render(<TimelineControls hour={2} length={10} playing={false} onHour={onHour} onPlaying={onPlaying} />);
    await userEvent.click(screen.getByRole("button", { name: "Next hour" }));
    expect(onHour).toHaveBeenCalledWith(3);
    await userEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(onPlaying).toHaveBeenCalledWith(true);
    expect(screen.getByRole("slider", { name: "Simulation hour" })).toHaveAttribute(
      "aria-valuetext",
      "Hour 2 of 9",
    );
  });
});

describe("Help panel", () => {
  it("renders the help section from the maintained docs and closes on Escape", async () => {
    const onClose = vi.fn();
    render(<HelpPanel open onClose={onClose} />);
    expect(screen.getByRole("dialog", { name: "Help" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Using TerraShield" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Approval" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("extracts only the marked section", () => {
    expect(extractHelp("a <!-- help:start -->HELP<!-- help:end --> b")).toBe("HELP");
  });
});
