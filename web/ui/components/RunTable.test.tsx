import { fireEvent, render, screen } from "@testing-library/react";
import { describe as group, expect, it, vi } from "vitest";
import { RunTable } from "./RunTable";
import type { RunRow } from "@/lib/types";

const runs: RunRow[] = [
  { id: 1, started_at: 0, ended_at: 30, wave: 12, coins: 500, tier: 2, abandoned: 0, scan_count: 10, tap_count: 4 },
  { id: 2, started_at: 30, ended_at: 90, wave: 15, coins: 700, tier: 2, abandoned: 0, scan_count: 20, tap_count: 6 },
];

group("RunTable", () => {
  it("calls onSelect with the clicked run's id", () => {
    const onSelect = vi.fn();
    render(<RunTable runs={runs} onSelect={onSelect} />);

    fireEvent.click(screen.getByText("15").closest("tr")!);

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("renders placeholder text when there are no stored runs", () => {
    render(<RunTable runs={[]} onSelect={vi.fn()} />);

    expect(screen.getByText(/No stored runs/)).toBeDefined();
  });
});
