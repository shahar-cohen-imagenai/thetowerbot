"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { EventFeed } from "@/components/EventFeed";
import { RunTable } from "@/components/RunTable";
import { fetchRunEvents, fetchRuns } from "@/lib/api";
import type { BotEvent, RunRow, StoredEvent } from "@/lib/types";

function Runs() {
  const router = useRouter();
  const params = useSearchParams();
  // Static export cannot prerender /runs/[id] - the ids do not exist at build
  // time - so the id rides in the query string instead. Same deep link, one
  // exported route, no SPA fallback needed in FastAPI.
  const selected = params.get("id");

  const [runs, setRuns] = useState<RunRow[]>([]);
  // Distinguishes "haven't heard back yet" from "heard back, no such run":
  // `runs` alone can't tell the two apart, since both start out empty.
  // Without this, /runs/?id=999 briefly - and then permanently, since the
  // fetch always resolves to the same empty match - looks identical to a
  // real run with no stored events.
  const [runsLoaded, setRunsLoaded] = useState(false);
  const [events, setEvents] = useState<StoredEvent[]>([]);

  useEffect(() => {
    fetchRuns(200)
      .then(setRuns)
      .catch(() => setRuns([]))
      .finally(() => setRunsLoaded(true));
  }, []);

  useEffect(() => {
    if (!selected) return setEvents([]);
    fetchRunEvents(Number(selected)).then(setEvents).catch(() => setEvents([]));
  }, [selected]);

  const run = runs.find((r) => String(r.id) === selected);

  return (
    <div className="flex flex-col gap-4">
      {selected ? (
        <>
          <button onClick={() => router.push("/runs/")} className="self-start rounded border px-2 py-1 text-sm">
            ← all runs
          </button>
          <h1 className="text-lg font-semibold">Run #{selected}</h1>
          {run ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {[
                ["wave", run.wave ?? "-"], ["coins", run.coins ?? "-"], ["tier", run.tier ?? "-"],
                ["taps", run.tap_count], ["scans", run.scan_count],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-lg border p-3">
                  <div className="text-xs uppercase text-muted-foreground">{label}</div>
                  <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
                </div>
              ))}
            </div>
          ) : runsLoaded ? (
            // Otherwise indistinguishable from a real run with no stored
            // events: no tiles and an empty feed either way.
            <p className="text-sm text-muted-foreground">No such run.</p>
          ) : null}
          {run || !runsLoaded ? (
            <EventFeed events={events.map((row) => ({ ...row, ...row.detail }) as unknown as BotEvent)} />
          ) : null}
        </>
      ) : (
        <RunTable runs={runs} onSelect={(id) => router.push(`/runs/?id=${id}`)} />
      )}
    </div>
  );
}

export default function RunsPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <Runs />
    </Suspense>
  );
}
