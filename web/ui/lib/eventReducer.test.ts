import { expect, it } from "vitest";
import { FEED_LIMIT, feedReducer } from "./eventReducer";
import type { BotEvent } from "./types";

const scan = (seq: number): BotEvent => ({
  type: "ScanCompleted", seq, ts: seq, screen: "IN_RUN", duration_ms: 40, wallet: null,
});

it("appends events in arrival order", () => {
  const state = feedReducer(feedReducer([], { kind: "event", event: scan(1) }), {
    kind: "event", event: scan(2),
  });
  expect(state.map((e) => e.seq)).toEqual([1, 2]);
});

it("drops the oldest beyond the limit so a long session cannot grow forever", () => {
  let state: BotEvent[] = [];
  for (let seq = 1; seq <= FEED_LIMIT + 10; seq += 1) {
    state = feedReducer(state, { kind: "event", event: scan(seq) });
  }
  expect(state).toHaveLength(FEED_LIMIT);
  expect(state[0].seq).toBe(11);
});

it("ignores an event it has already seen, so a reconnect replay cannot double up", () => {
  // EventSource replays from Last-Event-ID on reconnect, and the server's ring
  // is inclusive of anything the browser may already hold.
  let state = feedReducer([], { kind: "event", event: scan(1) });
  state = feedReducer(state, { kind: "event", event: scan(1) });
  expect(state).toHaveLength(1);
});

it("clears on demand", () => {
  const state = feedReducer([scan(1)], { kind: "clear" });
  expect(state).toEqual([]);
});
