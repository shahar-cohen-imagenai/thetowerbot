import { duration } from "@/lib/format";
import type { RunPurchasePayload } from "@/lib/types";

/** ATTACK/DEFENSE/UTILITY abbreviated for a summary line that has to fit on
 *  one row beside two other facts. An unknown category is not abbreviated -
 *  it is the catalog saying it has never heard of this upgrade, and hiding
 *  that behind a three-letter code would make it look like a fourth kind. */
const SHORT: Record<string, string> = {
  ATTACK: "ATK",
  DEFENSE: "DEF",
  UTILITY: "UTL",
};

/** A price that could not be read renders as a dash, never as $0: null is
 *  OCR failing, and a free upgrade is a real and different thing. */
const price = (n: number | null): string => (n === null ? "—" : "$" + n.toLocaleString());

/**
 * What one run bought inside the battle, and what it cost.
 *
 * Presentational and payload-in: the same component serves the live run on
 * the dashboard and a stored one on /runs, because the server answers both
 * from the same table. `data === null` means no record came back at all -
 * `--no-store`, a failed fetch, or a run old enough that the 30-day event
 * pruning has taken its purchases while the run row itself survives. That is
 * deliberately not rendered as "0 buys", which would be a claim the data
 * does not support.
 */
export function RunPurchases({
  data,
  startedAt,
}: {
  data: RunPurchasePayload | null;
  /** The run's start, so each buy is timed from the battle rather than the
   *  wall clock - "1:31 in" is the readable fact, not a timestamp. */
  startedAt: number;
}) {
  if (!data) {
    return <p className="text-sm text-muted-foreground">No purchase record for this run.</p>;
  }
  const { purchases, totals } = data;
  if (!purchases.length) {
    return <p className="text-sm text-muted-foreground">Nothing bought this run.</p>;
  }

  const split = Object.entries(totals.by_category)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => `${SHORT[name] ?? name} ${count}`)
    .join(" / ");

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-xs">
        <span className="text-foreground">{totals.count} buys</span>
        {/* Withheld when not one price was legible: `spent` is 0 there
            because nothing could be added up, and "$0" would report a run
            that spent nothing. A run that really did buy only free upgrades
            has unpriced === 0 and still shows its honest $0. */}
        {totals.unpriced < totals.count ? (
          <span className="text-muted-foreground">{"$" + totals.spent.toLocaleString()}</span>
        ) : null}
        {split ? <span className="text-muted-foreground">{split}</span> : null}
        {totals.unpriced ? (
          // Said out loud rather than folded into the total: without it the
          // spend reads as the whole run's, and it is only the part that
          // was legible.
          <span className="text-warn">
            {totals.unpriced} {totals.unpriced === 1 ? "price" : "prices"} unread
          </span>
        ) : null}
      </div>
      {/* Bounded and scrolled for the same reason RunTable is: a long run
          buys dozens of upgrades, and the card has to stay a card. */}
      <div className="max-h-72 overflow-y-auto">
        {/* Capped rather than full-width: this card spans the page on /runs,
            and a four-column table stretched across it puts a hand's width of
            empty space between an upgrade and its own price. */}
        <table className="w-full max-w-2xl text-sm">
          <thead className="sticky top-0 bg-background text-muted-foreground">
            <tr>{["at", "upgrade", "price", "value"].map((h) => (
              <th key={h} className="border-b py-1 text-left text-[10px] font-semibold uppercase tracking-[0.13em]">
                {h}
              </th>
            ))}</tr>
          </thead>
          <tbody>
            {purchases.map((purchase) => (
              <tr key={purchase.seq}>
                <td className="border-b py-1 font-mono tabular-nums text-muted-foreground">
                  {duration(purchase.ts - startedAt)}
                </td>
                <td className="border-b py-1">
                  {purchase.item ?? purchase.upgrade_id ?? "unknown"}
                </td>
                <td className="border-b py-1 font-mono tabular-nums">{price(purchase.price)}</td>
                <td className="border-b py-1 font-mono tabular-nums text-muted-foreground">
                  {purchase.value ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
