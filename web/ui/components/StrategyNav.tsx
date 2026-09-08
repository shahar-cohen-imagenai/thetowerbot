"use client";

import { SectionNav, type NavItem } from "@/components/SectionNav";
import type { Strategy } from "@/lib/types";

type Section = {
  id: string;
  label: string;
  /** "3/9" for rule lists, a plain count of settings otherwise. */
  count: (s: Strategy) => string;
  /** Which slice of the document this section owns, for the unsaved dot. */
  slice: (s: Strategy) => unknown;
};

const SECTIONS: Section[] = [
  {
    id: "purchases",
    label: "Purchases",
    count: (s) => s.autopilot?.enabled ? `${s.autopilot.rules.filter((a) => a.enabled).length}/${s.autopilot.rules.length}` : `${s.actions.filter((a) => a.enabled).length}/${s.actions.length}`,
    slice: (s) => [s.actions, s.affordability, s.autopilot],
  },
  {
    id: "timing",
    label: "Timing",
    count: () => "4",
    slice: (s) => [s.interval, s.click_cooldown, s.navigation_cooldown, s.screen_confirmations],
  },
  {
    id: "jitter",
    label: "Jitter",
    count: (s) => `${[s.tap_jitter_px, s.timing_jitter, s.tap_delay].filter((n) => n > 0).length}/3`,
    slice: (s) => [s.tap_jitter_px, s.timing_jitter, s.tap_delay],
  },
  {
    id: "run-policy",
    label: "Run policy",
    count: () => "2",
    slice: (s) => [s.auto_navigate, s.max_runs],
  },
  {
    id: "claims",
    label: "Claims",
    // A profile from a backend older than the scheduler has no claims block,
    // which is the same thing as the cadence being off.
    count: (s) => (s.claims?.enabled ? "on" : "off"),
    slice: (s) => s.claims,
  },
  {
    id: "shopping",
    label: "Shopping",
    count: (s) => `${s.shopping.workshop.filter((r) => r.enabled).length}/${s.shopping.workshop.length}`,
    slice: (s) => ({ ...s.shopping, cards: null }),
  },
  {
    id: "cards",
    label: "Cards",
    count: (s) => (s.shopping.cards.enabled ? "on" : "off"),
    slice: (s) => s.shopping.cards,
  },
  {
    id: "advisor",
    label: "Advisor",
    count: () => "",
    // Imports live outside the strategy; staged changes belong to Shopping.
    slice: () => null,
  },
];

/**
 * A map for what was otherwise a blind six-card scroll of ~20 controls.
 *
 * The dot is the part that earns its place: with the toolbar pinned at the
 * top, "unsaved" is visible from anywhere, but *which* section holds the
 * unsaved edit was not.
 */
export function StrategyNav({ draft, saved }: { draft: Strategy; saved: Strategy }) {
  const items: NavItem[] = SECTIONS.map((section) => ({
    id: section.id,
    label: section.label,
    meta: section.count(draft),
    dot: JSON.stringify(section.slice(draft)) !== JSON.stringify(section.slice(saved)),
  }));
  return <SectionNav items={items} />;
}
