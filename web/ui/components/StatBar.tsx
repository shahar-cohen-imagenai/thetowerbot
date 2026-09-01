import { money } from "@/lib/format";
import type { StatusPayload } from "@/lib/types";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

export function StatBar({ status, connected }: { status: StatusPayload | null; connected: boolean }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-sm">
        <span className={`inline-block size-2 rounded-full ${connected ? "bg-emerald-500" : "bg-red-500"}`} />
        {connected ? "live" : "reconnecting"}
        {status?.last_error ? <span className="ml-2 text-red-500">{status.last_error}</span> : null}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="screen" value={status?.screen ?? "-"} />
        <Stat label="uptime" value={status ? Math.round(status.uptime) + "s" : "-"} />
        <Stat label="scans" value={status ? status.scans : "-"} />
        <Stat label="runs" value={status ? status.runs_completed : "-"} />
        <Stat label="wallet" value={status ? money(status.wallet) : "-"} />
        <Stat label="dropped" value={status ? status.dropped : "-"} />
      </div>
    </div>
  );
}
