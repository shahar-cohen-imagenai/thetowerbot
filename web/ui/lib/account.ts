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
