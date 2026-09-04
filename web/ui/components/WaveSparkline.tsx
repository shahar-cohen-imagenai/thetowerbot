import { median } from "@/lib/useDerived";

/**
 * Wave reached, oldest to newest.
 *
 * Hand-drawn rather than a recharts chart: there are no axes, no tooltip and
 * no legend to earn, and pulling the charting library onto the Live page would
 * cost more than the whole rest of the page put together. What it does gain
 * over the bare polyline it replaces is a gradient fill, a median reference
 * line to read the last run against, and an emphasised endpoint.
 */
export function WaveSparkline({ waves }: { waves: number[] }) {
  if (waves.length < 2) {
    return <p className="text-sm text-muted-foreground">Not enough runs yet.</p>;
  }

  const top = Math.max(...waves);
  const floor = Math.min(...waves);
  const span = top - floor || 1;
  const y = (wave: number) => 44 - ((wave - floor) / span) * 40;
  const x = (i: number) => (i / (waves.length - 1)) * 100;

  const points = waves.map((wave, i) => `${x(i)},${y(wave)}`).join(" ");
  const par = median(waves);
  const last = waves[waves.length - 1];

  return (
    <div className="flex flex-col gap-1">
      <svg viewBox="0 0 100 48" preserveAspectRatio="none" className="h-20 w-full" aria-hidden="true">
        <defs>
          <linearGradient id="wave-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-1)" stopOpacity="0.4" />
            <stop offset="100%" stopColor="var(--chart-1)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {par != null ? (
          <line
            x1="0" y1={y(par)} x2="100" y2={y(par)}
            stroke="var(--muted-foreground)" strokeWidth="1" strokeDasharray="3 4"
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
        <polygon points={`0,48 ${points} 100,48`} fill="url(#wave-fill)" />
        <polyline
          points={points} fill="none" stroke="var(--chart-1)" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="flex justify-between font-mono text-[10px] text-faint-foreground">
        <span>{waves.length} runs</span>
        {par != null ? <span>median {Math.round(par)}</span> : null}
        <span className="text-chart-1">last {last}</span>
      </div>
    </div>
  );
}
