import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AccountScreenReadings, ScreenField } from "@/lib/account";
import { ScreenReadings } from "./ScreenReadings";

const field = (key: string, raw_value: string | null, status: ScreenField["status"] = "observed"): ScreenField => ({
  key, label: key, raw_value, status, confidence: .97, rect: [100, 200, 40, 30],
});
const readings: AccountScreenReadings = {
  current_screen_id: "account.stats.summary", error: null,
  readings: [{
    screen_id: "account.stats.summary", observed_at: 100, frame_width: 1080, frame_height: 2400,
    frame_digest: "test-frame", tiers: [], fields: [
      field("Coins Earned", "0"), field("Cells Earned Per Hour", "Need More Data", "insufficient_data"),
      field("Interest Earned", null, "unreadable"),
    ],
  }, {
    screen_id: "account.stats.tiers", observed_at: 90, frame_width: 1080, frame_height: 2400,
    frame_digest: "tier-frame", fields: [], tiers: [{
      tier: 3, wave: field("Wave", null, "unreadable"), coins: field("Coins", "0"), cells: field("Cells", "0", "unreadable"),
    }],
  }],
};

describe("screen observations", () => {
  it("distinguishes unsupported backend from an unscanned session", () => {
    const view = render(<ScreenReadings />);
    expect(screen.getByText(/Screen observations are unavailable in this backend/)).toBeDefined();
    view.rerender(<ScreenReadings data={{ current_screen_id: null, error: null, readings: [] }} />);
    expect(screen.getByText(/No supported Settings or Stats screen observed this session/)).toBeDefined();
  });

  it("keeps zero, insufficient data, and unreadable cells distinct", () => {
    render(<ScreenReadings data={readings} />);
    expect(screen.getByText("Need More Data")).toBeDefined();
    const table = screen.getByRole("table", { name: "Tier statistics" });
    const row = within(table).getAllByRole("row")[1];
    expect(within(row).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["Unreadable", "0", "Unreadable"]);
    expect(screen.getByText(/Not used for upgrade decisions/)).toBeDefined();
    expect(screen.getByText(/cleared when the backend restarts/)).toBeDefined();
  });

  it("labels retained readings separately and retains evidence after reader errors", () => {
    render(<ScreenReadings data={{ ...readings, current_screen_id: null, error: "OCR unavailable" }} />);
    expect(screen.getByRole("alert").textContent).toContain("OCR unavailable");
    expect(screen.queryByText("Visible at last refresh")).toBeNull();
    expect(screen.getAllByText("Retained observation")).toHaveLength(2);
    expect(screen.getByRole("alert").textContent).toContain("Current visibility is unconfirmed");
    expect(screen.getByText("test-frame")).toBeDefined();
    expect(screen.getAllByText(/Image not retained/)).toHaveLength(2);
  });
});
