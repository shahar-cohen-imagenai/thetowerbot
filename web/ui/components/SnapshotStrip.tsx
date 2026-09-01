import type { Snapshot } from "@/lib/types";

export function SnapshotStrip({ shots }: { shots: Snapshot[] }) {
  if (!shots.length) {
    return <p className="text-sm text-muted-foreground">No unknown screens captured.</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {shots.slice(0, 12).map((shot) => (
        // Plain <img>: next/image needs a loader, and these are local PNGs
        // served by the same FastAPI process.
        // eslint-disable-next-line @next/next/no-img-element
        <img key={shot.name} src={shot.url} alt={shot.name}
             title={new Date(shot.ts * 1000).toLocaleString()}
             className="w-24 rounded border" />
      ))}
    </div>
  );
}
