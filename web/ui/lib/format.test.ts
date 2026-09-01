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
});
