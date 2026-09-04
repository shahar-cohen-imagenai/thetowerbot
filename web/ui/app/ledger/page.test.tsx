// fireEvent, not @testing-library/user-event: user-event is not a
// dependency of this project and every other test here uses fireEvent.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe as group, expect, it, vi } from "vitest";
import LedgerPage from "./page";

const { fetchLedger } = vi.hoisted(() => ({ fetchLedger: vi.fn() }));

vi.mock("@/lib/api", () => ({ fetchLedger }));
vi.mock("@/lib/useEventStream", () => ({ useEventStream: () => ({ events: [], connected: true }) }));

function payload(overrides = {}) {
  return {
    lines: [], balances: { coins: null, gems: null }, rehearsals: 0, next: null,
    ...overrides,
  };
}

const A_PURCHASE = {
  id: 1, seq: 1, ts: 0, kind: "WORKSHOP_BUY", item: "Health",
  category: "DEFENSE", currency: "coins", delta: -75, price: 75,
  balance_after: 1695, observed: 1770, dry_run: 0, run_id: null, visit: 1,
  reason: null, detail: {},
};

group("LedgerPage", () => {
  it("shows an explicit empty state with nothing recorded", async () => {
    fetchLedger.mockResolvedValue(payload());
    render(<LedgerPage />);

    await screen.findByText("Nothing recorded yet.");
  });

  it("does not report an unreachable server as an empty account", async () => {
    fetchLedger.mockRejectedValue(new Error("/api/ledger -> 503"));
    render(<LedgerPage />);

    await screen.findByText(/could not load/i);
    expect(screen.queryByText("Nothing recorded yet.")).toBeNull();
  });

  it("lists a purchase with its delta and running balance", async () => {
    fetchLedger.mockResolvedValue(
      payload({ lines: [A_PURCHASE], balances: { coins: 1695, gems: 40 } }),
    );
    render(<LedgerPage />);

    await screen.findByText("Health");
    expect(screen.getByText("-75")).toBeDefined();
    expect(screen.getByText("1,695")).toBeDefined();
  });

  it("flags an unexplained line as the account moving outside the bot", async () => {
    fetchLedger.mockResolvedValue(payload({
      lines: [{
        ...A_PURCHASE, id: 2, seq: null, kind: "UNEXPLAINED", item: null,
        currency: "gems", delta: -140, price: null, balance_after: 40,
        observed: 40,
      }],
    }));
    render(<LedgerPage />);

    await screen.findByText("UNEXPLAINED");
    expect(screen.getByText(/outside the bot/i)).toBeDefined();
  });

  it("hides rehearsals until asked, and says how many there are", async () => {
    fetchLedger.mockResolvedValue(payload({ lines: [A_PURCHASE], rehearsals: 3 }));
    render(<LedgerPage />);

    const toggle = await screen.findByRole("button", { name: /show rehearsals \(3\)/i });
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(fetchLedger).toHaveBeenLastCalledWith({ includeRehearsals: true }),
    );
  });
});
