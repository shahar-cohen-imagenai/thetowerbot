import { describe as group, expect, it } from "vitest";
import { clock, describe, money } from "./format";

group("money", () => {
  it("renders a dash for absent values", () => {
    expect(money(null)).toBe("-");
    expect(money(undefined)).toBe("-");
  });
  it("prefixes a dollar sign", () => {
    expect(money(1200)).toBe("$1200");
  });
});

group("clock", () => {
  it("renders unix seconds as a wall clock", () => {
    // 1970-01-01T00:00:05Z, formatted in whatever zone the test runs in, so
    // assert the shape rather than the digits.
    expect(clock(5)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});

group("describe", () => {
  it("renders a tap with its score and price", () => {
    const line = describe({
      type: "Tapped", seq: 1, ts: 0, action: "Damage",
      x: 10, y: 20, score: 0.98123, price: 500, wallet: 900,
    });
    expect(line).toContain("TAP");
    expect(line).toContain("Damage");
    expect(line).toContain("0.981");
    expect(line).toContain("$500");
  });

  it("falls back to the bare type for an event it has never seen", () => {
    expect(describe({ type: "SomethingNew", seq: 2, ts: 0 } as never)).toBe("SomethingNew");
  });

  it("renders a screen change using `curr`", () => {
    const line = describe({
      type: "ScreenChanged", seq: 3, ts: 0,
      prev: "MENU", curr: "IN_RUN", confidence: 0.999, scores: {},
    });
    expect(line).toContain("MENU");
    expect(line).toContain("IN_RUN");
  });

  it("renders a control change with what moved and who moved it", () => {
    const line = describe({
      type: "ControlChanged", seq: 4, ts: 0,
      changed: { paused: true }, source: "web",
    });
    expect(line).toContain("paused=true");
    expect(line).toContain("web");
  });
});

group("splitEvent shopping events", () => {
  it("describes a purchase with what it cost and what it spent", () => {
    const line = describe({
      seq: 1, ts: 0, type: "Purchased", item: "Health", category: "DEFENSE",
      price: 75, coins_before: 1770, gems_before: null, dry_run: false,
    } as never);

    expect(line).toContain("BUY");
    expect(line).toContain("Health");
    expect(line).toContain("75");
  });

  it("marks a rehearsal so it cannot be read as a real purchase", () => {
    const line = describe({
      seq: 1, ts: 0, type: "Purchased", item: "Health", category: "DEFENSE",
      price: 75, coins_before: 1770, gems_before: null, dry_run: true,
    } as never);

    expect(line).toContain("rehearsal");
  });

  it("describes a skipped purchase with its reason", () => {
    const line = describe({
      seq: 1, ts: 0, type: "PurchaseSkipped", item: "Damage",
      reason: "unaffordable", detail: "", coins_before: 1770, gems_before: null,
    } as never);

    expect(line).toContain("NOBUY");
    expect(line).toContain("unaffordable");
  });

  it("describes the end of a shopping visit", () => {
    const line = describe({
      seq: 1, ts: 0, type: "ShoppingEnded", visit: 3, bought: 2, spent: 95,
      aborted: false, reason: "",
    } as never);

    expect(line).toContain("SHOP");
    expect(line).toContain("2");
    expect(line).toContain("95");
  });
});
