import type { BotEvent } from "./types";

/** Matches the server's SSE ring, so the browser never holds more than it. */
export const FEED_LIMIT = 500;

export type FeedAction =
  | { kind: "event"; event: BotEvent }
  | { kind: "clear" };

export function feedReducer(state: BotEvent[], action: FeedAction): BotEvent[] {
  if (action.kind === "clear") return [];

  // seq is monotonic and assigned by the bus, so it is the identity here.
  // Within one stream, events arrive strictly ascending - sinks/sse.py's
  // since() filters on seq > cursor - so seq relative to what we already
  // hold tells us which of two different things happened:
  const last = state.length ? state[state.length - 1].seq : 0;
  // Equal: the same event delivered again. Not something since() should
  // produce in normal operation, but cheap to guard against regardless.
  if (action.event.seq === last) return state;
  // Lower: the server's counter reset (--no-store, or a fresh --db swap -
  // see web/app.py's event_stream) and it replayed from the top of a new
  // ring, exactly as the server does for a stale Last-Event-ID. This is a
  // new session; the old feed is from a previous process and stale events
  // should not sit above live ones, so drop it and start over.
  if (action.event.seq < last) return [action.event];

  const next = [...state, action.event];
  return next.length > FEED_LIMIT ? next.slice(next.length - FEED_LIMIT) : next;
}
