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

it("ignores an event equal to the last one seen, as a defensive guard against repeated delivery", () => {
  // since()'s strict seq > cursor filter means a normal reconnect replay
  // should never actually repeat an event we hold - this is a cheap guard
  // against redelivery, not a scenario the server is known to produce.
  let state = feedReducer([], { kind: "event", event: scan(1) });
  state = feedReducer(state, { kind: "event", event: scan(1) });
  expect(state).toHaveLength(1);
});

it("starts a fresh feed when seq goes backwards, mirroring the server's counter-reset replay", () => {
  // web/app.py's event_stream resets a stale Last-Event-ID cursor to 0 and
  // replays from the top of a new ring when the bot restarted with
  // --no-store or a fresh --db (the seq counter starts over). Seeing a seq
  // lower than our high-water mark is how the client learns this happened -
  // within one stream seq only ever climbs - so the old feed is from a
  // previous process and gets discarded in favor of the new one.
  let state = feedReducer([], { kind: "event", event: scan(1) });
  state = feedReducer(state, { kind: "event", event: scan(2) });
  state = feedReducer(state, { kind: "event", event: scan(3) });
  state = feedReducer(state, { kind: "event", event: scan(1) });
  expect(state.map((e) => e.seq)).toEqual([1]);
});

it("clears on demand", () => {
  const state = feedReducer([scan(1)], { kind: "clear" });
  expect(state).toEqual([]);
});
