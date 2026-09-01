import type { RunRow } from "@/lib/types";

export function RunTable({ runs, onSelect }: { runs: RunRow[]; onSelect: (id: number) => void }) {
  if (!runs.length) {
    return <p className="text-sm text-muted-foreground">No stored runs. (Running with --no-store?)</p>;
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-muted-foreground">
        <tr>{["#", "wave", "coins", "tier", "taps", "length"].map((h) => (
          <th key={h} className="border-b py-1 text-left font-medium">{h}</th>
        ))}</tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.id} onClick={() => onSelect(run.id)} className="cursor-pointer hover:bg-accent/50">
            <td className="border-b py-1 tabular-nums">{run.id}</td>
            <td className="border-b py-1 tabular-nums">{run.wave ?? "-"}</td>
            <td className="border-b py-1 tabular-nums">{run.coins ?? "-"}</td>
            <td className="border-b py-1 tabular-nums">{run.tier ?? "-"}</td>
            <td className="border-b py-1 tabular-nums">{run.tap_count}</td>
            <td className="border-b py-1 tabular-nums">
              {run.ended_at ? Math.round(run.ended_at - run.started_at) + "s" : "live"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
