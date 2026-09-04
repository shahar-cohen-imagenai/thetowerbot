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
  | (EventBase & { type: "ControlChanged"; changed: Record<string, unknown>; source: string })
  | (EventBase & { type: "PageChanged"; prev_page: string; curr_page: string; confidence: number })
  | (EventBase & { type: "ShoppingStarted"; visit: number; dry_run: boolean })
  | (EventBase & { type: "ShoppingUnavailable"; reason: string })
  | (EventBase & { type: "Purchased"; item: string; category: string; price: number | null; coins_before: number | null; gems_before: number | null; dry_run: boolean })
  | (EventBase & { type: "PurchaseSkipped"; item: string; reason: string; detail: string; coins_before: number | null; gems_before: number | null })
  | (EventBase & { type: "ShoppingEnded"; visit: number; bought: number; spent: number; aborted: boolean; reason: string });

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
  /** Where a tap actually lands - the buy square beside the label, not the
   * label's own (x, y) origin. Absolute frame coordinates, same as x/y. */
  tap_x: number;
  tap_y: number;
  score: number;
  tapped: boolean;
}

export interface BotStatus {
  running: boolean;
  /** Unix seconds when the current bot started, or null when stopped. */
  since: number | null;
  /** The last start failure - a dead emulator, usually. Cleared by a
   * successful start. */
  error: string | null;
}

export interface StrategyList {
  active: string;
  names: string[];
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
  bot: BotStatus;
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

export interface ActionRule {
  name: string;
  template: string;
  enabled: boolean;
  threshold: number;
  brightness_ratio: number;
}

export interface ShoppingRule {
  name: string;
  template: string;
  category: "ATTACK" | "DEFENSE" | "UTILITY";
  /** Which of the two workshop layouts this row uses, which is what decides
   * where its price sits. A half-width upgrade row puts the price beside the
   * label; a full-width unlock tile centres it below. Not derivable from the
   * template path, so the row has to say. */
  layout: "row" | "tile";
  enabled: boolean;
  threshold: number;
  brightness_ratio: number;
}

export interface CardPolicy {
  enabled: boolean;
  gem_floor: number;
  max_per_visit: number;
  batch: "x1" | "x10";
}

/** Mirrors strategy.py's Shopping.to_dict(). `enabled` and `armed` are two
 * switches: enabled+unarmed reads and reports without tapping. */
export interface Shopping {
  enabled: boolean;
  armed: boolean;
  visit_every_n_runs: number;
  max_taps_per_visit: number;
  workshop: ShoppingRule[];
  cards: CardPolicy;
}

/** Mirrors strategy.py's Strategy.to_dict(). */
export interface Strategy {
  name: string;
  actions: ActionRule[];
  affordability: string;
  interval: number;
  click_cooldown: number;
  auto_navigate: boolean;
  max_runs: number | null;
  navigation_cooldown: number;
  screen_confirmations: number;
  tap_jitter_px: number;
  timing_jitter: number;
  tap_delay: number;
  shopping: Shopping;
}

export interface ControlPayload {
  paused: boolean;
  strategy: Strategy;
  affordability_available: string[];
  /** Non-null when this machine's header glyph atlas cannot support a
   * balance read, which makes ShoppingSession.begin() decline every visit
   * forever regardless of the policy - see build_shopping(). Lets the
   * Strategy page say why enabling and arming shopping produces total
   * silence, instead of doing nothing with no explanation. */
  shopping_disabled_reason: string | null;
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
