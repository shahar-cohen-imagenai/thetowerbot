import type { BotEvent } from "./types";

/** Matches the server's SSE ring, so the browser never holds more than it. */
export const FEED_LIMIT = 500;

export type FeedAction =
  | { kind: "event"; event: BotEvent }
  | { kind: "clear" };

export function feedReducer(state: BotEvent[], action: FeedAction): BotEvent[] {
  if (action.kind === "clear") return [];

  // seq is monotonic and assigned by the bus, so it is the identity here.
  // A reconnect replays from Last-Event-ID and can resend what we already
  // hold; without this the feed shows each replayed event twice.
  const last = state.length ? state[state.length - 1].seq : 0;
  if (action.event.seq <= last) return state;

  const next = [...state, action.event];
  return next.length > FEED_LIMIT ? next.slice(next.length - FEED_LIMIT) : next;
}
