import { describe, expect, it } from "vitest";
import { shouldResync } from "./useControlSync";

/** shouldResync is the whole subtlety of the hook, extracted so it can be
 * tested without a live EventSource. */
describe("shouldResync", () => {
  const evt = (seq: number, type: string) => ({ seq, type }) as never;

  it("fires when a ControlChanged arrives", () => {
    expect(shouldResync([evt(1, "ControlChanged")], 0)).toEqual({
      resync: true,
      seq: 1,
    });
  });

  it("fires when ControlChanged is not the last event in a batch", () => {
    // The server writes a whole sse.since() batch in one poll and React
    // batches the dispatches into one render, so checking only the tail
    // would miss a ControlChanged followed by a ScanCompleted.
    const batch = [evt(1, "ControlChanged"), evt(2, "ScanCompleted")];
    expect(shouldResync(batch, 0)).toEqual({ resync: true, seq: 2 });
  });

  it("does not re-fire for events already seen", () => {
    const batch = [evt(1, "ControlChanged"), evt(2, "ScanCompleted")];
    expect(shouldResync(batch, 2)).toEqual({ resync: false, seq: 2 });
  });

  it("treats a backwards seq as a fresh session and rescans everything", () => {
    // eventReducer resets the feed when the bus's seq counter moves
    // backwards - a bot restart. That must not wedge the high-water mark.
    expect(shouldResync([evt(1, "ControlChanged")], 500)).toEqual({
      resync: true,
      seq: 1,
    });
  });

  it("is a no-op on an empty feed", () => {
    expect(shouldResync([], 7)).toEqual({ resync: false, seq: 7 });
  });
});
