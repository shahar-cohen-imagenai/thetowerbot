import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe as group, expect, it, vi } from "vitest";
import LivePage from "./page";

// Fixtures are inlined inside the factories (rather than referencing outer
// consts) because vi.mock factories are hoisted above module-level
// declarations.
vi.mock("@/lib/api", () => ({
  fetchStatus: vi.fn(() => Promise.resolve({
    screen: "MENU", uptime: 10, scans: 1, taps: {}, skips: {}, runs_completed: 1,
    run: null, wallet: null, last_error: null, tail: [], dropped: 0,
    boxes: [], frame_size: null,
    // `bot` is required on StatusPayload and the server always sends it.
    // Omitting it here was what let StatBar keep an optional-chain the type
    // said could never be needed.
    bot: { running: false, since: null, error: null },
  })),
  fetchRuns: vi.fn(() => Promise.resolve([
    { id: 7, started_at: 0, ended_at: 30, wave: 12, coins: 500, tier: 2, abandoned: 0, scan_count: 10, tap_count: 4 },
  ])),
  fetchUnknown: vi.fn(() => Promise.resolve([])),
  // Shaped exactly as sinks/store.py's to_row() stores a ScreenChanged event:
  // `curr` moves into the `screen` column, and `prev`/`confidence`/`scores`
  // are left in the `detail` blob. This is the one shape (column plus blob)
  // that RunStarted alone doesn't exercise, and it's what regressed history
  // mode's rendering (see web/ui/lib/format.test.ts for the unit-level case).
  fetchRunEvents: vi.fn(() => Promise.resolve([
    {
      seq: 1, run_id: 7, ts: 0, type: "ScreenChanged", screen: "GAME", action: null,
      reason: null, score: null, price: null, wallet: null,
      detail: { prev: "MENU", confidence: 0.87, scores: {} },
    },
  ])),
}));

vi.mock("@/lib/useEventStream", () => ({
  useEventStream: () => ({ events: [], connected: true }),
}));

// The Live page pulls in four fetches and the SSE hook, so this test drives
// the page directly (with those mocked) rather than re-deriving the
// history-mode swap in a throwaway harness. RunTable's own onSelect contract
// is covered in isolation by components/RunTable.test.tsx.
group("LivePage history mode", () => {
  it("swaps the feed into history mode on row click, and 'back to live' restores it", async () => {
    render(<LivePage />);

    // Scoped to the table: the wave now also appears in the status strip, so
    // an unscoped search for "12" matches the tile as well as the row.
    const table = await screen.findByRole("table");
    const row = within(table).getByText("12").closest("tr");
    fireEvent.click(row!);

    await screen.findByText(/Replaying run #7/);
    // Names both screens (not "SCREEN MENU -> undefined"): the stored row's
    // `curr` came back from the `screen` column, not a top-level `curr` field.
    // Asserted on the row's title, which carries the whole composed line - the
    // visible row splits the type into its own chip, so the label and the
    // message are no longer in one element.
    const line = screen.getByTitle(/SCREEN\s+MENU -> GAME/);
    expect(line).toBeDefined();
    expect(line.textContent).not.toMatch(/undefined/);

    fireEvent.click(screen.getByText("back to live"));

    expect(screen.queryByText(/Replaying run #7/)).toBeNull();
    expect(screen.queryByTitle(/SCREEN\s+MENU -> GAME/)).toBeNull();
  });
});
