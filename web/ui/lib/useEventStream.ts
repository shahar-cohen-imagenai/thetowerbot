"use client";

import { useEffect, useReducer, useState } from "react";
import { feedReducer } from "./eventReducer";
import type { BotEvent } from "./types";

/** The single SSE subscription. One per tab - mount this once, share via context.
 *
 * EventSource reconnects on its own and replays Last-Event-ID, so a dropped
 * connection costs nothing as long as the gap fits inside the server's ring.
 */
export function useEventStream(): { events: BotEvent[]; connected: boolean } {
  const [events, dispatch] = useReducer(feedReducer, []);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const source = new EventSource("/api/events/stream");
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message) =>
      dispatch({ kind: "event", event: JSON.parse(message.data) as BotEvent });
    return () => source.close();
  }, []);

  return { events, connected };
}
