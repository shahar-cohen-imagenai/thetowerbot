import { render, screen } from "@testing-library/react";
import { describe as group, expect, it, vi } from "vitest";
import ErrorsPage from "./page";

const { fetchErrors, fetchUnknown } = vi.hoisted(() => ({
  fetchErrors: vi.fn(),
  fetchUnknown: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ fetchErrors, fetchUnknown }));

group("ErrorsPage", () => {
  it("shows an explicit empty state with no stored errors", async () => {
    fetchErrors.mockResolvedValue([]);
    fetchUnknown.mockResolvedValue([]);
    render(<ErrorsPage />);

    await screen.findByText("No errors recorded.");
    await screen.findByText(/No unknown screens captured/);
  });

  it("lists errors with an expandable traceback", async () => {
    fetchErrors.mockResolvedValue([
      {
        seq: 1, run_id: null, ts: 0, type: "BotError", screen: null, action: null,
        reason: null, score: null, price: null, wallet: null,
        detail: { message: "boom", traceback: "Traceback (most recent call last)…" },
      },
    ]);
    fetchUnknown.mockResolvedValue([]);
    render(<ErrorsPage />);

    await screen.findByText("boom");
    expect(screen.getByText("traceback")).toBeDefined();
    expect(screen.getByText(/Traceback \(most recent call last\)/)).toBeDefined();
  });
});
