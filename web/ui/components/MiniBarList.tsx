import { cn } from "@/lib/utils";

/**
 * A counted breakdown, biggest first.
 *
 * Built for /api/status's `taps` and `skips`, which are both
 * Record<name, count>. `skips` in particular is keyed by *reason*
 * (unaffordable, cooldown, wrong_screen), which makes this the panel that
 * answers "the bot is running but nothing is happening - why?". The payload
 * has carried it every two seconds since the dashboard was built; nothing
 * rendered it until now.
 */
export function MiniBarList({
  title,
  counts,
  limit = 5,
  /** Colours the largest bar amber. Used for skips, where the dominant reason
   *  is the explanation rather than just the tallest bar. */
  highlightLeader = false,
  empty,
}: {
  title: string;
  counts: Record<string, number>;
  limit?: number;
  highlightLeader?: boolean;
  empty: string;
}) {
  const rows = Object.entries(counts)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit);
  const top = rows.length ? rows[0][1] : 0;
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);

  return (
    <div className="min-w-0">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.13em] text-faint-foreground">
          {title}
        </span>
        {total > 0 ? (
          <span className="font-mono text-[10px] text-faint-foreground">{total}</span>
        ) : null}
      </div>

      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {rows.map(([name, count], index) => (
            <li key={name} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
              <span className="truncate font-mono text-[11px] text-muted-foreground" title={name}>
                {name}
              </span>
              <span className="font-mono text-[11px] tabular-nums">{count}</span>
              <span className="col-span-2 h-1.5 overflow-hidden rounded-full bg-well">
                <span
                  className={cn(
                    "block h-full rounded-full",
                    highlightLeader && index === 0 ? "bg-warn" : highlightLeader ? "bg-muted-foreground/60" : "bg-chart-1",
                  )}
                  style={{ width: `${top ? (count / top) * 100 : 0}%` }}
                />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
