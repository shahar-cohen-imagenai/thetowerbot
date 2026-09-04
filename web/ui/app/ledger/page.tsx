"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { SectionCard } from "@/components/ui/section-card";
import { fetchLedger } from "@/lib/api";
import { clock } from "@/lib/format";
import type { LedgerPayload } from "@/lib/types";
import { useEventStream } from "@/lib/useEventStream";
import { cn } from "@/lib/utils";

/** The event types that change the ledger. The page refetches when one
 *  arrives rather than deriving a line in the browser: the catalog and the
 *  balance arithmetic live in ledger.py, and a second implementation here
 *  would be a second thing to get wrong. */
const LEDGER_EVENTS = new Set([
  "Purchased", "PurchaseSkipped", "ShoppingStarted", "ShoppingEnded",
  "ShoppingUnavailable", "ControlChanged", "RunEnded",
]);

const KIND_TONE: Record<string, string> = {
  UNEXPLAINED: "bg-warn-surface text-warn",
  WORKSHOP_BUY: "bg-chart-2/15 text-chart-2",
  CARD_BUY: "bg-chart-2/15 text-chart-2",
  RUN_PAYOUT: "bg-live-surface text-live",
  BUY_SKIPPED: "bg-muted text-muted-foreground",
};

const num = (value: number | null) =>
  value === null ? "—" : value.toLocaleString("en-US");

export default function LedgerPage() {
  const [data, setData] = useState<LedgerPayload | null>(null);
  // A failed fetch must never fall into the same empty state as a healthy
  // account - reporting an unreachable server as "nothing recorded" would
  // be the same defect the Errors page calls out.
  const [failed, setFailed] = useState<string | null>(null);
  const [rehearsals, setRehearsals] = useState(false);
  const { events } = useEventStream();

  const load = useCallback(() => {
    fetchLedger(rehearsals ? { includeRehearsals: true } : {})
      .then((payload) => { setData(payload); setFailed(null); })
      .catch((e: Error) => setFailed(e.message));
  }, [rehearsals]);

  useEffect(load, [load]);

  // Refetch on the last event only, not the whole array: the feed grows on
  // every scan and re-running this for each one would hammer the route.
  const latest = events[events.length - 1];
  useEffect(() => {
    if (latest && LEDGER_EVENTS.has(latest.type)) load();
  }, [latest, load]);

  const lines = data?.lines ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Ledger"
        meta="everything outside a run"
        action={
          data ? (
            <span className="font-mono text-xs text-muted-foreground">
              {num(data.balances.coins)} coins · {num(data.balances.gems)} gems
            </span>
          ) : null
        }
      />

      <SectionCard
        title="History"
        tone={failed ? "warn" : undefined}
        action={
          data?.rehearsals ? (
            <button
              type="button"
              onClick={() => setRehearsals((on) => !on)}
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
            >
              {rehearsals
                ? `hide rehearsals (${data.rehearsals})`
                : `show rehearsals (${data.rehearsals})`}
            </button>
          ) : null
        }
      >
        {failed ? (
          <div className="rounded-md border border-warn bg-warn-surface p-3">
            <p className="text-sm text-warn">Could not load the ledger.</p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{failed}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              This is not the same as an empty account — the dashboard could not
              reach the bot.
            </p>
          </div>
        ) : lines.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-[0.12em] text-faint-foreground">
                  <th className="py-1 pr-3 font-medium">Time</th>
                  <th className="py-1 pr-3 font-medium">Kind</th>
                  <th className="py-1 pr-3 font-medium">Item</th>
                  <th className="py-1 pr-3 text-right font-medium">Δ</th>
                  <th className="py-1 pr-3 text-right font-medium">Balance</th>
                  <th className="py-1 font-medium">Note</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((line) => (
                  <tr key={line.id} className="border-t">
                    <td className="py-1.5 pr-3 font-mono text-[11px] text-faint-foreground">
                      {clock(line.ts)}
                    </td>
                    <td className="py-1.5 pr-3">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 font-mono text-[10px]",
                          KIND_TONE[line.kind] ?? "bg-muted text-muted-foreground",
                        )}
                      >
                        {line.kind}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3">
                      {line.item ?? "—"}
                      {line.dry_run ? (
                        <span className="ml-1.5 text-xs text-muted-foreground">
                          rehearsal
                        </span>
                      ) : null}
                    </td>
                    <td
                      className={cn(
                        "py-1.5 pr-3 text-right font-mono",
                        line.delta !== null && line.delta > 0 && "text-live",
                        line.delta !== null && line.delta < 0 && "text-muted-foreground",
                      )}
                    >
                      {line.delta === null ? "?" : line.delta > 0 ? `+${line.delta}` : line.delta}
                    </td>
                    <td className="py-1.5 pr-3 text-right font-mono text-muted-foreground">
                      {num(line.balance_after)}
                      {line.currency ? (
                        <span className="ml-1 text-[10px] text-faint-foreground">
                          {line.currency}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-1.5 text-xs text-muted-foreground">
                      {line.kind === "UNEXPLAINED"
                        ? "balance moved outside the bot"
                        : line.reason ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>
        )}
      </SectionCard>
    </div>
  );
}
