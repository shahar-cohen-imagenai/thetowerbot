"use client";

import { createContext, useContext, useEffect, useReducer, useState } from "react";
import { feedReducer } from "./eventReducer";
import type { BotEvent } from "./types";

/** Two contexts, not one, and that split is the point.
 *
 * `connected` changes twice a session; `events` changes once or twice a
 * second. A single context would re-render every consumer - the nav rail on
 * every page included - on every scan the bot completes, just to keep a
 * two-pixel dot honest. */
const EventsContext = createContext<BotEvent[]>([]);
const ConnectedContext = createContext(false);

/**
 * The single SSE subscription for the tab.
 *
 * EventSource reconnects on its own and replays Last-Event-ID, so a dropped
 * connection costs nothing as long as the gap fits inside the server's ring.
 *
 * This lives in the root layout, which is what lets `useEventStream` be called
 * from more than one place - the Live feed, useControlSync, and the rail's
 * connection badge - without each caller opening a socket of its own.
 */
export function EventStreamProvider({ children }: { children: React.ReactNode }) {
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

  return (
    <ConnectedContext.Provider value={connected}>
      <EventsContext.Provider value={events}>{children}</EventsContext.Provider>
    </ConnectedContext.Provider>
  );
}

/** The stream, for callers that read the events themselves.
 *
 * With no provider above it this reads as an empty, disconnected stream -
 * which is exactly how the UI renders an unreachable bot, so a missing
 * provider shows up as a dot stuck on "reconnecting" rather than as a crash. */
export function useEventStream(): { events: BotEvent[]; connected: boolean } {
  return { events: useContext(EventsContext), connected: useContext(ConnectedContext) };
}

/** Just the connection, for callers that only report liveness. Subscribing to
 *  this instead of the whole stream is what keeps the rail from re-rendering
 *  on every event. */
export function useConnected(): boolean {
  return useContext(ConnectedContext);
}
