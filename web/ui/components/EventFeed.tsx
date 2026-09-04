"use client";

import { ArrowDown, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { clock, describe, splitEvent } from "@/lib/format";
import type { BotEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

/** The hue lives in the type chip, not in the message. Colouring whole lines
 *  by type turns a busy feed into 400px of one colour, which is both ugly and
 *  unreadable; the chip carries the same information in 76px. */
const CHIP: Record<string, string> = {
  TAP: "bg-live-surface text-live",
  SKIP: "bg-muted text-muted-foreground",
  SCREEN: "bg-chart-1/15 text-chart-1",
  NAV: "bg-chart-1/15 text-chart-1",
  RUN: "bg-chart-4/15 text-chart-4",
  ERROR: "bg-danger-surface text-danger",
  UNKNWN: "bg-danger-surface text-danger",
  CTRL: "bg-primary/15 text-primary",
  SCAN: "bg-transparent text-faint-foreground",
};

export function EventFeed({
  events,
  /** False while replaying a stored run, which swaps the whole array at once
   *  and must not be mistaken for the bot restarting. */
  live = true,
}: {
  events: BotEvent[];
  live?: boolean;
}) {
  const [term, setTerm] = useState("");
  const [unread, setUnread] = useState(0);
  const [restarted, setRestarted] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const mounted = useRef(false);
  const highWater = useRef(0);

  // Auto-scroll only when the reader is already at the bottom. Scrolling a
  // reader away from the line they are looking at is the fastest way to make
  // a live feed useless.
  useEffect(() => {
    const node = box.current;
    if (node && pinned.current) {
      node.scrollTop = node.scrollHeight;
      setUnread(0);
    } else if (mounted.current) {
      setUnread((n) => n + 1);
    }
    mounted.current = true;
  }, [events]);

  // feedReducer drops the whole feed when the bus sequence goes backwards -
  // the bot restarted and is replaying a new ring. To the reader that just
  // looked like the feed emptying itself, with no marker and no explanation.
  useEffect(() => {
    if (!live) {
      highWater.current = 0;
      return;
    }
    const last = events.length ? events[events.length - 1].seq : 0;
    if (last < highWater.current) setRestarted(true);
    highWater.current = last;
  }, [events, live]);

  const needle = term.trim().toLowerCase();
  const rows = events
    .map((event) => ({ event, line: `${clock(event.ts)} ${describe(event)}` }))
    .filter(({ line }) => needle === "" || line.toLowerCase().includes(needle));

  const newest = rows.length ? rows[rows.length - 1].event.seq : null;

  // Which run each row belongs to, so rows from one run share a left edge.
  let current: number | null = null;
  const runOf = new Map<number, number | null>();
  for (const { event } of rows) {
    if (event.type === "RunStarted") current = event.run_id;
    runOf.set(event.seq, current);
    if (event.type === "RunEnded") current = null;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="relative w-56">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-faint-foreground"
        />
        <input
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          placeholder="filter…"
          className="w-full rounded-md border bg-well py-1 pl-7 pr-2 text-sm"
        />
      </div>

      <div className="relative">
        <div
          ref={box}
          onScroll={(e) => {
            const node = e.currentTarget;
            pinned.current = node.scrollTop + node.clientHeight >= node.scrollHeight - 20;
            if (pinned.current) setUnread(0);
          }}
          className="h-80 overflow-y-auto rounded-md border bg-well py-1 font-mono text-xs lg:h-[26rem]"
        >
          {restarted ? (
            <div className="flex items-center gap-2 px-3 py-1.5 text-[10px] uppercase tracking-[0.12em] text-warn">
              stream restarted
              <span className="h-px flex-1 bg-warn/30" />
            </div>
          ) : null}

          {rows.map(({ event, line }) => {
            const { kind, body } = splitEvent(event);

            // A RunStarted *is* the boundary, so it is drawn as one rather
            // than as a row followed by a separate divider saying the same
            // thing twice.
            if (event.type === "RunStarted") {
              return (
                <div
                  key={event.seq}
                  className="flex items-center gap-2 px-3 pb-1 pt-2 text-[10px] uppercase tracking-[0.12em] text-chart-4"
                >
                  <span className="text-faint-foreground">{clock(event.ts)}</span>
                  RUN #{event.run_id}
                  <span className="h-px flex-1 bg-chart-4/25" />
                </div>
              );
            }

            return (
              <div
                key={event.seq}
                title={line}
                className={cn(
                  "grid grid-cols-[4.5rem_4.25rem_minmax(0,1fr)] items-baseline gap-2 border-b border-border/40 px-3 py-0.5",
                  runOf.get(event.seq) != null && "border-l-2 border-l-chart-1/30",
                  // Only the newest row, and only for rows that arrived after
                  // mount - otherwise the whole backlog flashes on first paint.
                  mounted.current && event.seq === newest && "motion-safe:animate-arrive",
                )}
              >
                <span className="text-faint-foreground">{clock(event.ts)}</span>
                <span
                  className={cn(
                    "rounded-sm px-1 py-0.5 text-center text-[9px] font-bold tracking-[0.08em]",
                    CHIP[kind] ?? "bg-muted text-muted-foreground",
                  )}
                >
                  {kind}
                </span>
                <span className={cn("truncate", kind === "ERROR" ? "text-danger" : "text-foreground")}>
                  {body}
                </span>
              </div>
            );
          })}
        </div>

        {unread > 0 ? (
          <button
            type="button"
            onClick={() => {
              const node = box.current;
              if (!node) return;
              node.scrollTop = node.scrollHeight;
              pinned.current = true;
              setUnread(0);
            }}
            className="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-full bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground shadow-sm"
          >
            <ArrowDown className="size-3" aria-hidden="true" />
            {unread} new
          </button>
        ) : null}
      </div>
    </div>
  );
}
