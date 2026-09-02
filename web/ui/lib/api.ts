import type {
  BotStatus,
  ControlPayload,
  RunRow,
  Snapshot,
  StatsPayload,
  StatusPayload,
  Strategy,
  StrategyList,
  StoredEvent,
} from "./types";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return (await response.json()) as T;
}

export const fetchStatus = () => getJson<StatusPayload>("/api/status");
export const fetchRuns = (limit = 30) => getJson<RunRow[]>(`/api/runs?limit=${limit}`);
export const fetchRunEvents = (id: number) => getJson<StoredEvent[]>(`/api/runs/${id}/events`);
export const fetchUnknown = () => getJson<Snapshot[]>("/api/unknown");
export const fetchStats = () => getJson<StatsPayload>("/api/stats");
export const fetchErrors = (limit = 100) => getJson<StoredEvent[]>(`/api/errors?limit=${limit}`);

export const fetchControl = () => getJson<ControlPayload>("/api/control");

/** FastAPI's `detail` is a plain string when our own ControlError becomes a
 * 422 (e.g. an out-of-range interval), but a list of structured items when
 * pydantic itself rejects a field's type before our validation ever runs
 * (e.g. a string where a float belongs). Flatten either into one line. */
function describeDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : JSON.stringify(item),
      )
      .join("; ");
  }
  return null;
}

/** Returns the full new state, or throws with the server's reason. */
export async function patchControl(
  patch: Partial<Strategy> & { paused?: boolean },
): Promise<ControlPayload> {
  const response = await fetch("/api/control", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(describeDetail(body.detail) ?? `PATCH /api/control -> ${response.status}`);
  return body as ControlPayload;
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  // 204 and empty bodies are not expected from any of these routes, but a
  // failed parse must still surface as the status, not as a JSON error.
  const parsed = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      (parsed && describeDetail(parsed.detail)) ?? `${method} ${path} -> ${response.status}`,
    );
  }
  return parsed as T;
}

export const fetchStrategies = () => getJson<StrategyList>("/api/strategies");
export const fetchStrategy = (name: string) =>
  getJson<Strategy>(`/api/strategies/${encodeURIComponent(name)}`);
export const saveStrategy = (name: string, body: Strategy) =>
  send<Strategy>(`/api/strategies/${encodeURIComponent(name)}`, "PUT", body);
export const activateStrategy = (name: string) =>
  send<StrategyList>(`/api/strategies/${encodeURIComponent(name)}/activate`, "POST");
export const deleteStrategy = (name: string) =>
  send<StrategyList>(`/api/strategies/${encodeURIComponent(name)}`, "DELETE");

/** Starts the bot. A 409 (already running - another tab may have started
 * one) is a normal answer, not swallowed here: it surfaces via send()'s
 * error like any other rejection, with the server's own message. */
export const startBot = () => send<BotStatus>("/api/bot/start", "POST");

/** Ends the bot but keeps the dashboard serving. Distinct from `shutdown()`,
 * which ends the whole process - see that function's own comment. */
export const stopBot = () => send<BotStatus>("/api/bot/stop", "POST");

/**
 * Ends the process, bot and dashboard together - which is what the control
 * page's stop button confirms ("Stop the bot and the dashboard?").
 *
 * The lifecycle split gave the bot itself its own start/stop
 * (`startBot`/`stopBot` above); this is the separate, more drastic action of
 * ending the dashboard process.
 */
export const shutdown = () => send<{ stopping: boolean }>("/api/shutdown", "POST");
