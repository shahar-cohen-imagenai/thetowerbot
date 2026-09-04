import { fireEvent, render, screen } from "@testing-library/react";
import { describe as group, expect, it, vi } from "vitest";
import RunsPage from "./page";

const { push, searchParams } = vi.hoisted(() => ({
  push: vi.fn(),
  searchParams: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => searchParams,
}));

vi.mock("@/lib/api", () => ({
  fetchRuns: vi.fn(() => Promise.resolve([
    { id: 1, started_at: 0, ended_at: 30, wave: 12, coins: 500, tier: 2, abandoned: 0, scan_count: 10, tap_count: 4 },
  ])),
  fetchRunEvents: vi.fn(() => Promise.resolve([
    {
      seq: 1, run_id: 1, ts: 0, type: "RunStarted", screen: null, action: null,
      reason: null, score: null, price: null, wallet: null, detail: { run_id: 1 },
    },
  ])),
}));

group("RunsPage", () => {
  it("lists runs and navigates to the query-string deep link on row click", async () => {
    render(<RunsPage />);

    const row = (await screen.findByText("12")).closest("tr");
    fireEvent.click(row!);

    expect(push).toHaveBeenCalledWith("/runs/?id=1");
  });

  it("shows a run's detail and events when ?id is present", async () => {
    searchParams.set("id", "1");
    render(<RunsPage />);

    await screen.findByText("Run #1");
    // Stat tiles pulled from the matching run row.
    expect(screen.getByText("12")).toBeDefined(); // wave
    // A RunStarted is drawn as the feed's run-boundary divider ("RUN #1")
    // rather than as an ordinary row, so it reads as the start of a group.
    await screen.findByText(/RUN\s+#1/);

    searchParams.delete("id");
  });

  it("shows a not-found state for an id with no matching run", async () => {
    searchParams.set("id", "999");
    render(<RunsPage />);

    await screen.findByText("Run #999");
    await screen.findByText("No such run.");
    // Indistinguishable-from-real-empty-run tiles/feed must not render either.
    expect(screen.queryByText("12")).toBeNull();

    searchParams.delete("id");
  });
});
