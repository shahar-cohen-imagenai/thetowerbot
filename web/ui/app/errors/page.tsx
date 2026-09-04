"use client";

import { useEffect, useState } from "react";
import { SnapshotStrip } from "@/components/SnapshotStrip";
import { SectionCard } from "@/components/ui/section-card";
import { fetchErrors, fetchUnknown } from "@/lib/api";
import { clock } from "@/lib/format";
import type { Snapshot, StoredEvent } from "@/lib/types";

export default function ErrorsPage() {
  const [errors, setErrors] = useState<StoredEvent[]>([]);
  const [shots, setShots] = useState<Snapshot[]>([]);
  // A failed fetch used to fall into the same empty array as a healthy bot,
  // so an unreachable server rendered as "No errors recorded." - the most
  // dangerous thing this page could possibly say.
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    fetchErrors()
      .then((rows) => { setErrors(rows); setFailed(null); })
      .catch((e: Error) => setFailed(e.message));
    fetchUnknown().then(setShots).catch(() => setShots([]));
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <SectionCard
        title="Errors"
        tone={failed ? "warn" : errors.length ? "danger" : undefined}
        action={
          errors.length ? (
            <span className="font-mono text-xs text-muted-foreground">{errors.length}</span>
          ) : null
        }
      >
        {failed ? (
          <div className="rounded-md border border-warn bg-warn-surface p-3">
            <p className="text-sm text-warn">Could not load errors.</p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{failed}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              This is not the same as having no errors — the dashboard could not reach the bot.
            </p>
          </div>
        ) : errors.length ? (
          <ul className="flex flex-col gap-2">
            {errors.map((row) => (
              // The edge carries the severity, not the body copy: a wall of
              // red 14px prose costs contrast and is tiring to read.
              <li key={row.seq} className="rounded-md border border-l-[3px] border-l-danger p-2.5">
                <div className="font-mono text-[11px] text-faint-foreground">{clock(row.ts)}</div>
                <div className="mt-0.5 text-sm">{String(row.detail.message ?? "")}</div>
                {row.detail.traceback ? (
                  <details className="mt-1.5">
                    <summary className="cursor-pointer text-xs text-muted-foreground">traceback</summary>
                    <pre className="mt-1 overflow-x-auto rounded-md bg-well p-2 text-xs">
                      {String(row.detail.traceback)}
                    </pre>
                  </details>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No errors recorded.</p>
        )}
      </SectionCard>

      <SectionCard title="Unknown screens">
        <SnapshotStrip shots={shots} />
      </SectionCard>
    </div>
  );
}
