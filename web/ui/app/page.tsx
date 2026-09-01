"use client";

import { useCallback, useEffect, useState } from "react";
import { EventFeed } from "@/components/EventFeed";
import { RunTable } from "@/components/RunTable";
import { SnapshotStrip } from "@/components/SnapshotStrip";
import { StatBar } from "@/components/StatBar";
import { WaveSparkline } from "@/components/WaveSparkline";
import { fetchRunEvents, fetchRuns, fetchStatus, fetchUnknown } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";
import type { BotEvent, RunRow, Snapshot, StatusPayload } from "@/lib/types";

/** Re-run `load` now and every `ms` thereafter, until unmounted. */
function usePoll(load: () => Promise<void>, ms: number) {
  useEffect(() => {
    let alive = true;
    const tick = () => { if (alive) void load(); };
    tick();
    const id = setInterval(tick, ms);
    return () => { alive = false; clearInterval(id); };
  }, [load, ms]);
}

export default function LivePage() {
  const { events, connected } = useEventStream();
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [shots, setShots] = useState<Snapshot[]>([]);
  // Non-null means the feed is showing one stored run instead of the live stream.
  const [history, setHistory] = useState<{ id: number; events: BotEvent[] } | null>(null);

  usePoll(useCallback(async () => setStatus(await fetchStatus()), []), 2000);
  usePoll(useCallback(async () => setRuns(await fetchRuns(30)), []), 15000);
  usePoll(useCallback(async () => setShots(await fetchUnknown()), []), 60000);

  async function showRun(id: number) {
    const stored = await fetchRunEvents(id);
    // Stored rows are columns plus a detail blob; flatten them back into the
    // shape describe() takes, so one renderer serves live and history both.
    // Blob last: it holds no column's name except `detail` itself, whose
    // string value is the one the renderer wants back.
    setHistory({ id, events: stored.map((row) => ({ ...row, ...row.detail }) as unknown as BotEvent) });
  }

  const waves = runs.filter((r) => r.wave != null).map((r) => r.wave as number).reverse();

  return (
    <div className="flex flex-col gap-4">
      <StatBar status={status} connected={connected} />

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border p-3">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Current run</h2>
          {status?.run ? (
            <p className="font-mono text-sm">
              #{status.run.id} · {Math.round(status.run.elapsed)}s ·{" "}
              {Object.entries(status.run.taps).map(([k, v]) => `${k} x${v}`).join(" · ") || "no taps yet"}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">idle</p>
          )}
        </section>

        <section className="rounded-lg border p-3">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Waves</h2>
          <WaveSparkline waves={waves} />
        </section>
      </div>

      <section className="rounded-lg border p-3">
        <h2 className="mb-2 flex items-center gap-3 text-xs uppercase tracking-wide text-muted-foreground">
          Events
          {history ? (
            <>
              <span>— run #{history.id}</span>
              <button onClick={() => setHistory(null)} className="rounded border px-2 py-0.5 normal-case">
                back to live
              </button>
            </>
          ) : null}
        </h2>
        <EventFeed events={history ? history.events : events} />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border p-3">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Run history</h2>
          <RunTable runs={runs} onSelect={showRun} />
        </section>

        <section className="rounded-lg border p-3">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Unknown screens</h2>
          <SnapshotStrip shots={shots} />
        </section>
      </div>
    </div>
  );
}
