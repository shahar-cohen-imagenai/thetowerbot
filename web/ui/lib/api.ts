import type { ControlPayload, RunRow, Snapshot, StatsPayload, StatusPayload, Strategy, StoredEvent } from "./types";

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

export async function stopBot(): Promise<void> {
  const response = await fetch("/api/control/stop", { method: "POST" });
  if (!response.ok) throw new Error(`POST /api/control/stop -> ${response.status}`);
}
