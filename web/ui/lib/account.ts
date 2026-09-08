/** B03's wire contract. Null sections mean unknown, never an empty inventory. */
export interface AccountEvidence {
  observed_at: number; confidence: number; raw_name: string; raw_value: string | null;
  rect: [number, number, number, number]; frame_width: number; frame_height: number;
  frame_digest: string; frame_ref: string | null;
}
export interface AccountFact {
  concept_id: string; value: number | string | boolean | null; status: string; evidence: AccountEvidence;
}
export const ACCOUNT_SECTIONS = {
  workshop_stats: "Workshop values", workshop_levels: "Workshop levels", lab_levels: "Lab levels",
  effective_account_stats: "Effective account stats", inventory: "Inventory", unlocks: "Unlocks", settings: "Settings",
} as const;
export type AccountSection = keyof typeof ACCOUNT_SECTIONS;
export type AccountRevision = Record<AccountSection, AccountFact[] | null> & {
  revision_id: number | null; parent_revision_id: number | null; created_at: number | null;
  account_id: string | null; game_version: string | null; registry_version: string;
};
export interface AccountSnapshot {
  persistence_available: boolean; error: string | null; errors: { account: string | null; run: string | null };
  revision: AccountRevision | null; unknown_state: AccountRevision | null;
  screen_readings?: AccountScreenReadings;
  /** Absent means this backend cannot walk the game at all - not "idle". */
  collection?: StatsCollection;
}
export interface CollectionResult {
  status: string; reason: string; detail: string; screen_id: string | null; finished_at: number;
}
export interface StatsCollection {
  status: "idle" | "running" | "completed" | "failed";
  step: string; requested_at: number | null; trail: string[]; result: CollectionResult | null;
}
/** Says what the transaction did, never what the account contains. A stopped
 *  run reports where it stopped; it never implies a value was read. */
export function describeCollection(collection: StatsCollection): string {
  const step = collection.step.replace(/_/g, " ");
  if (collection.status === "running") return `Walking the game now: ${step}. All other automation is held.`;
  const result = collection.result;
  if (!result) return "No collection has run this session.";
  if (result.status === "completed") return `Last run read ${result.screen_id ?? "a Stats panel"} and returned to the main menu.`;
  return `Last run stopped (${result.reason.replace(/_/g, " ")}). ${result.detail}`;
}
/** One claim walk's progress. Mirrors MissionsClaim.snapshot() and
 *  MilestonesClaim.snapshot() - they are structurally identical. */
export interface ClaimSnapshot {
  status: string;
  step: string;
  requested_at: number | null;
  claimed: number;
  trail: string[];
  result: Record<string, unknown> | null;
}
export interface ScreenField {
  key: string; label: string; raw_value: string | null;
  status: "observed" | "insufficient_data" | "unreadable";
  confidence: number; rect: [number, number, number, number] | null;
}
export interface AccountScreenReading {
  screen_id: "account.settings" | "account.stats.summary" | "account.stats.tiers";
  observed_at: number; frame_width: number; frame_height: number; frame_digest: string;
  fields: ScreenField[];
  tiers: { tier: number; wave: ScreenField; coins: ScreenField; cells: ScreenField }[];
}
export interface AccountScreenReadings {
  current_screen_id: string | null; readings: AccountScreenReading[]; error: string | null;
}
export interface AccountConcept {
  concept_id: string; name: string; domain: string; kind: string; unit: string | null;
  prerequisites: string[] | null; unlocks: string[]; execution_scopes: string[];
  rule_verified: boolean;
}
export interface ConceptCatalog { registry_version: string; concepts: AccountConcept[] }
export const STALE_AFTER_SECONDS = 24 * 60 * 60;
export function evidenceAge(fact: AccountFact, now: number): string {
  const age = now - fact.evidence.observed_at;
  if (!Number.isFinite(age) || age < 0) return "Evidence time uncertain";
  return age >= STALE_AFTER_SECONDS ? "Stale saved evidence" : "Recent saved evidence";
}
export function readerSupported(concept: AccountConcept): boolean {
  return concept.kind === "stat" && concept.execution_scopes.includes("workshop");
}
