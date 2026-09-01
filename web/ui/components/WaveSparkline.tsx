export function WaveSparkline({ waves }: { waves: number[] }) {
  if (waves.length < 2) {
    return <p className="text-sm text-muted-foreground">Not enough runs yet.</p>;
  }
  const top = Math.max(...waves) || 1;
  const points = waves
    .map((wave, i) => `${(i / (waves.length - 1)) * 100},${40 - (wave / top) * 36}`)
    .join(" ");
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-16 w-full">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1" className="text-sky-500" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
