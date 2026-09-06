import { render, screen } from "@testing-library/react";
import { describe as group, expect, it } from "vitest";
import { RunPurchases } from "./RunPurchases";
import type { RunPurchasePayload } from "@/lib/types";

const payload = (over: Partial<RunPurchasePayload> = {}): RunPurchasePayload => ({
  purchases: [
    { seq: 1, ts: 130, item: "Damage", upgrade_id: "damage", price: 1200, value: 42, category: "ATTACK" },
    { seq: 2, ts: 191, item: "Health", upgrade_id: "health", price: 900, value: 1100, category: "DEFENSE" },
  ],
  totals: { count: 2, spent: 2100, unpriced: 0, by_category: { ATTACK: 1, DEFENSE: 1 } },
  ...over,
});

group("RunPurchases", () => {
  it("says nothing was bought rather than showing an empty table", () => {
    render(
      <RunPurchases
        data={payload({ purchases: [], totals: { count: 0, spent: 0, unpriced: 0, by_category: {} } })}
        startedAt={100}
      />,
    );

    expect(screen.getByText(/nothing bought/i)).toBeDefined();
  });

  it("lists every purchase with its item and price", () => {
    render(<RunPurchases data={payload()} startedAt={100} />);

    expect(screen.getByText("Damage")).toBeDefined();
    expect(screen.getByText("Health")).toBeDefined();
    expect(screen.getByText("$1,200")).toBeDefined();
    expect(screen.getByText("$900")).toBeDefined();
  });

  it("times each purchase from the run's own start, not the wall clock", () => {
    render(<RunPurchases data={payload()} startedAt={100} />);

    expect(screen.getByText("0:30")).toBeDefined();
    expect(screen.getByText("1:31")).toBeDefined();
  });

  it("shows the count, the total spent and the category split", () => {
    render(<RunPurchases data={payload()} startedAt={100} />);

    expect(screen.getByText(/2 buys/)).toBeDefined();
    expect(screen.getByText(/\$2,100/)).toBeDefined();
    expect(screen.getByText(/ATK 1/)).toBeDefined();
    expect(screen.getByText(/DEF 1/)).toBeDefined();
  });

  // A price of null is OCR that could not read the number, not a free
  // upgrade. Rendering it as $0 would put a bargain in the log and make the
  // total look like it covered every buy when it did not.
  it("marks an unread price instead of showing it as free", () => {
    render(
      <RunPurchases
        data={payload({
          purchases: [
            { seq: 1, ts: 130, item: "Damage", upgrade_id: "damage", price: null, value: 42, category: "ATTACK" },
          ],
          totals: { count: 1, spent: 0, unpriced: 1, by_category: { ATTACK: 1 } },
        })}
        startedAt={100}
      />,
    );

    expect(screen.queryByText("$0")).toBeNull();
    expect(screen.getByText(/1 price unread/)).toBeDefined();
  });

  it("says the record is missing rather than claiming zero buys when there is none", () => {
    render(<RunPurchases data={null} startedAt={100} />);

    expect(screen.getByText(/no purchase record/i)).toBeDefined();
  });
});
