"use client";

import { useState } from "react";
import { SnapshotLightbox } from "@/components/SnapshotLightbox";
import type { Snapshot } from "@/lib/types";

export function SnapshotStrip({ shots }: { shots: Snapshot[] }) {
  const [selected, setSelected] = useState<Snapshot | null>(null);

  if (!shots.length) {
    return <p className="text-sm text-muted-foreground">No unknown screens captured.</p>;
  }
  return (
    <>
      <div className="flex flex-wrap gap-2">
        {shots.slice(0, 12).map((shot) => (
          // A button rather than a bare <img> with a handler: these are the
          // only way into the full-size view, so they have to be reachable
          // by keyboard and announced as something you can press.
          <button
            key={shot.name}
            type="button"
            aria-label={shot.name}
            title={new Date(shot.ts * 1000).toLocaleString()}
            onClick={() => setSelected(shot)}
            className="rounded ring-offset-2 transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {/* Plain <img>: next/image needs a loader, and these are local PNGs
                served by the same FastAPI process. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={shot.url} alt="" className="w-24 rounded border" />
          </button>
        ))}
      </div>
      {selected && <SnapshotLightbox shot={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
