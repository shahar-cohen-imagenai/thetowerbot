import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShoppingEditor } from "./ShoppingEditor";
import type { Shopping } from "@/lib/types";

const policy: Shopping = {
  enabled: true,
  armed: false,
  visit_every_n_runs: 1,
  max_taps_per_visit: 40,
  workshop: [
    { name: "Unlock Cash Bonuses", template: "workshop/unlock_cash_bonuses.png",
      category: "UTILITY", layout: "tile", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
    { name: "Damage", template: "workshop/row_damage.png",
      category: "ATTACK", layout: "row", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
  ],
  cards: { enabled: false, gem_floor: 40, max_per_visit: 2, batch: "x1" },
};

describe("ShoppingEditor", () => {
  it("shows rows in priority order", () => {
    render(<ShoppingEditor shopping={policy} onChange={vi.fn()} />);
    const rows = screen.getAllByTestId("shopping-row");
    expect(rows[0]).toHaveTextContent("Unlock Cash Bonuses");
  });

  it("says plainly that an unarmed policy spends nothing", () => {
    render(<ShoppingEditor shopping={policy} onChange={vi.fn()} />);
    expect(screen.getByText(/rehearsal/i)).toBeInTheDocument();
  });

  it("requires a confirmation before arming", () => {
    const onChange = vi.fn();
    render(<ShoppingEditor shopping={policy} onChange={onChange} />);
    fireEvent.click(screen.getByRole("switch", { name: /arm/i }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /yes, spend coins/i }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ armed: true }),
    );
  });

  it("disarms without asking", () => {
    const onChange = vi.fn();
    render(<ShoppingEditor shopping={{ ...policy, armed: true }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("switch", { name: /arm/i }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ armed: false }));
  });

  it("carries a hint explaining why each row sits where it does", () => {
    render(<ShoppingEditor shopping={policy} onChange={vi.fn()} />);
    expect(screen.getByTestId("hint-Unlock Cash Bonuses")).toHaveTextContent(/unlock/i);
  });

  it("shows layout as read-only text, not an editable control", () => {
    render(<ShoppingEditor shopping={policy} onChange={vi.fn()} />);
    const row = screen.getAllByTestId("shopping-row")[0];
    expect(row).toHaveTextContent(/tile/i);
    expect(row.querySelector("select")).toBeNull();
    expect(row.querySelector('input[value="tile"]')).toBeNull();
  });

  it("warns that the gem floor guards a currency with no refund", () => {
    render(<ShoppingEditor shopping={policy} onChange={vi.fn()} />);
    expect(screen.getByLabelText(/gem floor/i)).toBeInTheDocument();
    expect(screen.getByText(/cannot be earned back quickly/i)).toBeInTheDocument();
  });
});
