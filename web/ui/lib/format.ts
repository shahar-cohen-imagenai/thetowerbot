import type { BotEvent } from "./types";

export const clock = (ts: number): string =>
  new Date(ts * 1000).toTimeString().slice(0, 8);

export const money = (n: number | null | undefined): string =>
  n === null || n === undefined ? "-" : "$" + n;

/** One event as one line of the feed. */
export function describe(event: BotEvent): string {
  switch (event.type) {
    case "Tapped":
      return `TAP    ${event.action} (${event.x},${event.y}) score=${event.score.toFixed(3)} price=${money(event.price)}`;
    case "Skipped":
      return `SKIP   ${event.action} reason=${event.reason}${event.detail ? " " + event.detail : ""}`;
    case "ScreenChanged": {
      // A live event carries `curr`. A replayed history row does not:
      // sinks/store.py's to_row() moves `curr` into the events table's
      // `screen` column before blobbing the rest, so a stored row has
      // `screen` instead. Prefer `curr`, fall back to `screen`.
      const curr = (event as { curr?: string; screen?: string }).curr ??
        (event as { curr?: string; screen?: string }).screen;
      return `SCREEN ${event.prev} -> ${curr} (${event.confidence.toFixed(3)})`;
    }
    case "ScanCompleted":
      return `SCAN   ${event.screen} ${Math.round(event.duration_ms)}ms wallet=${money(event.wallet)}`;
    case "RunStarted":
      return `RUN    #${event.run_id} started`;
    case "RunEnded":
      return `RUN    #${event.run_id} ${event.abandoned ? "abandoned" : "ended"} wave=${event.wave ?? "?"} coins=${event.coins ?? "?"}`;
    case "Navigated":
      return `NAV    ${event.target}`;
    case "UnknownScreen":
      return `UNKNWN best=${event.best_anchor} ${event.best_score.toFixed(3)}`;
    case "BotError":
      return `ERROR  ${event.message}`;
    case "ControlChanged": {
      const parts = Object.entries(event.changed)
        .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
        .join(" ");
      return `CTRL   ${parts} (${event.source})`;
    }
    default:
      // An event type the UI predates. Showing its name beats dropping it.
      return (event as { type: string }).type;
  }
}
