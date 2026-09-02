"use client";

import { useEffect, useRef } from "react";
import { useEventStream } from "./useEventStream";
import type { BotEvent } from "./types";

/** Whether a batch of newly-arrived events means we should re-fetch, and the
 * new high-water seq.
 *
 * Checking only the last element is not enough: the server writes a whole
 * sse.since() batch in one poll, EventSource dispatches those messages within
 * one browser task, and React batches the resulting dispatches into a single
 * render - so a ControlChanged followed by a ScanCompleted in the same batch
 * would leave a non-ControlChanged event at the tail and a tail check would
 * never fire.
 *
 * A newest seq LOWER than the high-water mark is a bot restart: eventReducer
 * resets the feed when the bus's seq counter moves backwards. Treat the whole
 * freshly-reset array as new rather than filtering it all away, or the mark
 * stays wedged above every event of the new session. */
export function shouldResync(
  events: readonly BotEvent[],
  lastSeen: number,
): { resync: boolean; seq: number } {
  if (events.length === 0) return { resync: false, seq: lastSeen };
  const latest = events[events.length - 1].seq;
  const sessionReset = latest < lastSeen;
  const fresh = sessionReset ? events : events.filter((e) => e.seq > lastSeen);
  return { resync: fresh.some((e) => e.type === "ControlChanged"), seq: latest };
}

/** Call `onChange` whenever another tab (or another client) changes the
 * controls, so this tab converges without polling. */
export function useControlSync(onChange: () => void): void {
  const { events } = useEventStream();
  const lastSeenSeq = useRef(0);
  useEffect(() => {
    const { resync, seq } = shouldResync(events, lastSeenSeq.current);
    lastSeenSeq.current = seq;
    if (resync) onChange();
    // onChange is expected to be a useCallback; a fresh identity every
    // render would re-run this on every event and defeat the high-water mark.
  }, [events, onChange]);
}
