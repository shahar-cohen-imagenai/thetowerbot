import { fireEvent, render, screen } from "@testing-library/react";
import { describe as group, expect, it, vi } from "vitest";
import LivePage from "./page";

// Fixtures are inlined inside the factories (rather than referencing outer
// consts) because vi.mock factories are hoisted above module-level
// declarations.
vi.mock("@/lib/api", () => ({
  fetchStatus: vi.fn(() => Promise.resolve({
    screen: "MENU", uptime: 10, scans: 1, taps: {}, skips: {}, runs_completed: 1,
    run: null, wallet: null, last_error: null, tail: [], dropped: 0,
  })),
  fetchRuns: vi.fn(() => Promise.resolve([
    { id: 7, started_at: 0, ended_at: 30, wave: 12, coins: 500, tier: 2, abandoned: 0, scan_count: 10, tap_count: 4 },
  ])),
  fetchUnknown: vi.fn(() => Promise.resolve([])),
  fetchRunEvents: vi.fn(() => Promise.resolve([
    {
      seq: 1, run_id: 7, ts: 0, type: "RunStarted", screen: null, action: null,
      reason: null, score: null, price: null, wallet: null, detail: { run_id: 7 },
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

    const row = (await screen.findByText("12")).closest("tr");
    fireEvent.click(row!);

    await screen.findByText(/— run #7/);
    expect(screen.getByText(/RUN\s+#7 started/)).toBeDefined();

    fireEvent.click(screen.getByText("back to live"));

    expect(screen.queryByText(/— run #7/)).toBeNull();
    expect(screen.queryByText(/RUN\s+#7 started/)).toBeNull();
  });
});
