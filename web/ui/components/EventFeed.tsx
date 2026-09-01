"use client";

import { useEffect, useRef, useState } from "react";
import { clock, describe } from "@/lib/format";
import type { BotEvent } from "@/lib/types";

const TONE: Record<string, string> = {
  Tapped: "text-emerald-500",
  Skipped: "text-muted-foreground",
  ScreenChanged: "text-sky-500",
  Navigated: "text-sky-500",
  RunStarted: "text-amber-500",
  RunEnded: "text-amber-500",
  BotError: "text-red-500",
  UnknownScreen: "text-red-500",
};

export function EventFeed({ events }: { events: BotEvent[] }) {
  const [term, setTerm] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // Auto-scroll only when the reader is already at the bottom. Scrolling a
  // reader away from the line they are looking at is the fastest way to make
  // a live feed useless.
  useEffect(() => {
    const node = box.current;
    if (node && pinned.current) node.scrollTop = node.scrollHeight;
  }, [events]);

  const needle = term.trim().toLowerCase();
  const rows = events
    .map((event) => ({ event, line: `${clock(event.ts)} ${describe(event)}` }))
    .filter(({ line }) => needle === "" || line.toLowerCase().includes(needle));

  return (
    <div className="flex flex-col gap-2">
      <input
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        placeholder="filter…"
        className="w-48 rounded-md border bg-transparent px-2 py-1 text-sm"
      />
      <div
        ref={box}
        onScroll={(e) => {
          const node = e.currentTarget;
          pinned.current = node.scrollTop + node.clientHeight >= node.scrollHeight - 20;
        }}
        className="h-80 overflow-y-auto rounded-md border p-2 font-mono text-xs"
      >
        {rows.map(({ event, line }) => (
          <div key={event.seq} className={`whitespace-pre-wrap break-words ${TONE[event.type] ?? ""}`}>
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}
