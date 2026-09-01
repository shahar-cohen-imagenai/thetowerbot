// Mirrors of the Python payloads. Every event field here comes from the
// dataclasses in events.py: sinks/sse.py's to_payload() is dataclasses.asdict()
// plus a `type` discriminator, so these are the wire shapes exactly.

export interface EventBase {
  seq: number;
  ts: number;
}

export type BotEvent =
  | (EventBase & { type: "ScreenChanged"; prev: string; curr: string; confidence: number; scores: Record<string, number> })
  | (EventBase & { type: "ScanCompleted"; screen: string; duration_ms: number; wallet: number | null })
  | (EventBase & { type: "Tapped"; action: string; x: number; y: number; score: number; price: number | null; wallet: number | null })
  | (EventBase & { type: "Skipped"; action: string; reason: string; detail: string })
  | (EventBase & { type: "RunStarted"; run_id: number })
  | (EventBase & { type: "RunEnded"; run_id: number; duration: number; wave: number | null; coins: number | null; tier: number | null; abandoned: boolean })
  | (EventBase & { type: "Navigated"; target: string })
  | (EventBase & { type: "UnknownScreen"; snapshot_path: string; best_anchor: string; best_score: number })
  | (EventBase & { type: "BotError"; message: string; traceback: string })
  | (EventBase & { type: "ControlChanged"; changed: Record<string, unknown>; source: string });

/** A row from the `events` table, which carries columns plus a JSON blob. */
export interface StoredEvent {
  seq: number;
  run_id: number | null;
  ts: number;
  type: string;
  screen: string | null;
  action: string | null;
  reason: string | null;
  score: number | null;
  price: number | null;
  wallet: number | null;
  detail: Record<string, unknown>;
}

export interface CurrentRun {
  id: number;
  started_at: number;
  elapsed: number;
  taps: Record<string, number>;
}

export interface MatchBox {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  score: number;
  tapped: boolean;
}

export interface StatusPayload {
  screen: string;
  uptime: number;
  scans: number;
  taps: Record<string, number>;
  skips: Record<string, number>;
  runs_completed: number;
  run: CurrentRun | null;
  wallet: number | null;
  last_error: string | null;
  tail: unknown[];
  dropped: number;
  boxes: MatchBox[];
  frame_size: { width: number; height: number } | null;
}

/** A row from the `runs` table. */
export interface RunRow {
  id: number;
  started_at: number;
  ended_at: number | null;
  wave: number | null;
  coins: number | null;
  tier: number | null;
  abandoned: number;
  scan_count: number;
  tap_count: number;
}

export interface Snapshot {
  name: string;
  ts: number;
  url: string;
}

export interface ControlPayload {
  paused: boolean;
  interval: number;
  auto_navigate: boolean;
  strategy: string;
  enabled_actions: string[];
  actions: string[];
  strategies_available: string[];
}

export interface RunStat {
  id: number;
  started_at: number;
  ended_at: number;
  wave: number | null;
  coins: number | null;
  tier: number | null;
  tap_count: number;
  scan_count: number;
  duration: number;
}

export interface StatsPayload {
  runs: RunStat[];
  taps: { action: string; count: number }[];
  screens: { screen: string; count: number }[];
}
