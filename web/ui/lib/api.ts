import type { RunRow, Snapshot, StatusPayload, StoredEvent } from "./types";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return (await response.json()) as T;
}

export const fetchStatus = () => getJson<StatusPayload>("/api/status");
export const fetchRuns = (limit = 30) => getJson<RunRow[]>(`/api/runs?limit=${limit}`);
export const fetchRunEvents = (id: number) => getJson<StoredEvent[]>(`/api/runs/${id}/events`);
export const fetchUnknown = () => getJson<Snapshot[]>("/api/unknown");
