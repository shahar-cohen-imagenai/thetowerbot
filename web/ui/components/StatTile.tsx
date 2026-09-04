"use client";

import { useChangeFlash } from "@/lib/useDerived";
import { cn } from "@/lib/utils";

const TONE = {
  live: "border-t-live",
  warn: "border-t-warn",
  danger: "border-t-danger",
  none: "border-t-border-strong",
} as const;

const VALUE_TONE = {
  live: "text-live",
  warn: "text-warn",
  danger: "text-danger",
  none: "",
} as const;

/** A faint trend line behind the value. Not an axis and not a chart - it is
 *  there to say "rising" or "flat" in peripheral vision, nothing more. */
function Trend({ points }: { points: number[] }) {
  if (points.length < 2) return null;
  const top = Math.max(...points);
  const floor = Math.min(...points);
  const span = top - floor || 1;
  const path = points
    .map((value, i) => `${(i / (points.length - 1)) * 100},${28 - ((value - floor) / span) * 24}`)
    .join(" ");
  return (
    <svg
      viewBox="0 0 100 30"
      preserveAspectRatio="none"
      aria-hidden="true"
      className="pointer-events-none absolute inset-x-0 bottom-0 h-8 w-full opacity-15"
    >
      <polyline
        points={path}
        fill="none"
        stroke="var(--chart-1)"
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/**
 * One number, labelled.
 *
 * The label is human copy and set in sans; the value is machine output and set
 * in mono with lining figures, so a column of these lines up. Replaces the two
 * byte-identical local implementations that were in StatBar and the run detail
 * header.
 */
export function StatTile({
  label,
  value,
  unit,
  sub,
  subTone = "none",
  tone = "none",
  trend,
  size = "default",
  className,
}: {
  label: string;
  value: string | number;
  /** Set small and muted beside the value - "/s", "s", "%". */
  unit?: string;
  /** The second line: a delta, a duration, a qualifier. */
  sub?: string;
  subTone?: keyof typeof TONE;
  tone?: keyof typeof TONE;
  trend?: number[];
  size?: "default" | "hero";
  className?: string;
}) {
  const flash = useChangeFlash(value);

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-lg border border-t-2 bg-card p-3 transition-colors",
        TONE[tone],
        flash && "motion-safe:bg-primary/8",
        className,
      )}
    >
      {trend ? <Trend points={trend} /> : null}
      <div className="relative text-[10px] font-semibold uppercase tracking-[0.13em] text-faint-foreground">
        {label}
      </div>
      <div className="relative mt-1.5 flex items-baseline gap-1">
        <span
          className={cn(
            "font-mono font-medium leading-none tracking-tight",
            size === "hero" ? "text-2xl" : "text-lg",
            VALUE_TONE[tone],
          )}
        >
          {value}
        </span>
        {unit ? <span className="font-mono text-xs text-faint-foreground">{unit}</span> : null}
      </div>
      {sub ? (
        <div className={cn("relative mt-1 font-mono text-[11px]", subTone === "none" ? "text-faint-foreground" : VALUE_TONE[subTone])}>
          {sub}
        </div>
      ) : null}
    </div>
  );
}
