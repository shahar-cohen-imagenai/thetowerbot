import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AutopilotStatusPanel } from "./AutopilotStatus";
import type { AutopilotSnapshot } from "@/lib/types";

const snapshot: AutopilotSnapshot = {
  phase: "survival",
  reason: "Defense needs a larger buffer",
  next_upgrade_id: "defense_absolute",
  category: "DEFENSE",
  observations: [],
  combat: {},
  updated_at: 100,
  verified_purchases: 2,
  last_purchase: null,
};
describe("AutopilotStatus", () => {
  it("distinguishes an old decision from fresh observations", () => {
    render(<AutopilotStatusPanel snapshot={snapshot} now={140000} />);
    expect(screen.getByText("Stale · 40s ago")).toBeTruthy();
    expect(screen.getByText(snapshot.reason)).toBeTruthy();
    expect(screen.getByText("defense absolute")).toBeTruthy();
  });
  it("keeps the last known decision clearly disconnected", () => {
    render(
      <AutopilotStatusPanel snapshot={snapshot} now={101000} unreachable />,
    );
    expect(screen.getByText("Disconnected · last known state")).toBeTruthy();
    expect(screen.queryByText(/Fresh/)).toBeNull();
  });
});
