import { cn } from "@/lib/utils";

/**
 * Placeholder rows, shaped like the content that is coming.
 *
 * Replaces three separate "Loading…" string literals. Deliberately static
 * rather than shimmering: this dashboard is left open for hours, and a
 * pulsing block in peripheral vision reads as something happening when
 * nothing is.
 */
export function Skeleton({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-2", className)} aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="h-4 rounded bg-muted"
          // Ragged widths so it reads as absent content rather than as a
          // rendered table of empty rows.
          style={{ width: `${92 - (i % 3) * 14}%` }}
        />
      ))}
    </div>
  );
}
