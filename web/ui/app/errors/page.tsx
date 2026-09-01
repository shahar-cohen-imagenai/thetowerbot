"use client";

import { useEffect, useState } from "react";
import { SnapshotStrip } from "@/components/SnapshotStrip";
import { fetchErrors, fetchUnknown } from "@/lib/api";
import { clock } from "@/lib/format";
import type { Snapshot, StoredEvent } from "@/lib/types";

export default function ErrorsPage() {
  const [errors, setErrors] = useState<StoredEvent[]>([]);
  const [shots, setShots] = useState<Snapshot[]>([]);

  useEffect(() => {
    fetchErrors().then(setErrors).catch(() => setErrors([]));
    fetchUnknown().then(setShots).catch(() => setShots([]));
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-lg border p-3">
        <h2 className="mb-3 text-xs uppercase tracking-wide text-muted-foreground">Errors</h2>
        {errors.length ? (
          <ul className="flex flex-col gap-2">
            {errors.map((row) => (
              <li key={row.seq} className="rounded border p-2">
                <div className="font-mono text-xs text-muted-foreground">{clock(row.ts)}</div>
                <div className="text-sm text-red-500">{String(row.detail.message ?? "")}</div>
                {row.detail.traceback ? (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-xs text-muted-foreground">traceback</summary>
                    <pre className="mt-1 overflow-x-auto text-xs">{String(row.detail.traceback)}</pre>
                  </details>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No errors recorded.</p>
        )}
      </section>

      <section className="rounded-lg border p-3">
        <h2 className="mb-3 text-xs uppercase tracking-wide text-muted-foreground">Unknown screens</h2>
        <SnapshotStrip shots={shots} />
      </section>
    </div>
  );
}
