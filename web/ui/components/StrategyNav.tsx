"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { Strategy } from "@/lib/types";

type Section = {
  id: string;
  label: string;
  /** "3/9 on" for rule lists, a plain count of settings otherwise. */
  count: (s: Strategy) => string;
  /** Which slice of the document this section owns, for the unsaved dot. */
  slice: (s: Strategy) => unknown;
};

const SECTIONS: Section[] = [
  {
    id: "purchases",
    label: "Purchases",
    count: (s) => `${s.actions.filter((a) => a.enabled).length}/${s.actions.length}`,
    slice: (s) => [s.actions, s.affordability],
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
];

/**
 * A map for what was otherwise a blind six-card scroll of ~20 controls.
 *
 * The dot is the part that earns its place: with the toolbar pinned at the
 * top, "unsaved" is visible from anywhere, but *which* section holds the
 * unsaved edit was not.
 */
export function StrategyNav({ draft, saved }: { draft: Strategy; saved: Strategy }) {
  const [current, setCurrent] = useState<string>(SECTIONS[0].id);

  useEffect(() => {
    // jsdom has no IntersectionObserver; the nav is a convenience, so skip
    // the highlight rather than making the page unrenderable in tests.
    if (typeof IntersectionObserver === "undefined") return;
    const seen = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) seen.set(entry.target.id, entry.intersectionRatio);
        let best: string | null = null;
        let bestRatio = 0;
        for (const [id, ratio] of seen) {
          if (ratio > bestRatio) { best = id; bestRatio = ratio; }
        }
        if (best) setCurrent(best);
      },
      { threshold: [0, 0.25, 0.5, 1] },
    );
    for (const section of SECTIONS) {
      const node = document.getElementById(section.id);
      if (node) observer.observe(node);
    }
    return () => observer.disconnect();
  }, []);

  return (
    <nav className="sticky top-4 hidden w-44 shrink-0 flex-col gap-0.5 self-start xl:flex">
      <div className="px-2.5 pb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.14em] text-faint-foreground">
        Sections
      </div>
      {SECTIONS.map((section) => {
        const dirty = JSON.stringify(section.slice(draft)) !== JSON.stringify(section.slice(saved));
        return (
          <a
            key={section.id}
            href={`#${section.id}`}
            className={cn(
              "flex items-center gap-2 rounded-md border-l-2 border-transparent px-2.5 py-1.5 text-sm transition-colors",
              current === section.id
                ? "border-l-primary bg-primary/12 text-foreground"
                : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
            )}
          >
            <span className="truncate">{section.label}</span>
            {dirty ? (
              <span
                className="size-1.5 shrink-0 rounded-full bg-warn"
                aria-label="unsaved changes in this section"
              />
            ) : null}
            <span className="ml-auto font-mono text-[10px] text-faint-foreground">
              {section.count(draft)}
            </span>
          </a>
        );
      })}
    </nav>
  );
}
