"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export type NavItem = {
  /** Must match the id on the corresponding SectionCard. */
  id: string;
  label: string;
  /** A count, a state word - set in mono at the far end. */
  meta?: string;
  /** An amber dot: this section has something the reader should know about. */
  dot?: boolean;
};

/**
 * A sticky map of the sections on a long page.
 *
 * Shared by /strategy (six cards, ~20 controls, where the dot means unsaved)
 * and /guide (five long prose sections). Hidden below xl, where there is no
 * room for it and the page is a single scroll anyway.
 */
export function SectionNav({ items, label = "Sections" }: { items: NavItem[]; label?: string }) {
  const [current, setCurrent] = useState<string>(items[0]?.id ?? "");

  useEffect(() => {
    // jsdom has no IntersectionObserver; the nav is a convenience, so skip
    // the highlight rather than making the page unrenderable in tests.
    if (typeof IntersectionObserver === "undefined") return;
    const ratios = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) ratios.set(entry.target.id, entry.intersectionRatio);
        let best: string | null = null;
        let bestRatio = 0;
        for (const [id, ratio] of ratios) {
          if (ratio > bestRatio) { best = id; bestRatio = ratio; }
        }
        if (best) setCurrent(best);
      },
      { threshold: [0, 0.25, 0.5, 1] },
    );
    for (const item of items) {
      const node = document.getElementById(item.id);
      if (node) observer.observe(node);
    }
    return () => observer.disconnect();
    // Ids are static per page; re-observing on every render would thrash.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.map((i) => i.id).join(",")]);

  return (
    <nav
      aria-label={label}
      className="sticky top-4 hidden w-44 shrink-0 flex-col gap-0.5 self-start xl:flex"
    >
      <div className="px-2.5 pb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.14em] text-faint-foreground">
        {label}
      </div>
      {items.map((item) => (
        <a
          key={item.id}
          href={`#${item.id}`}
          aria-current={current === item.id ? "true" : undefined}
          className={cn(
            "flex items-center gap-2 rounded-md border-l-2 border-transparent px-2.5 py-1.5 text-sm transition-colors",
            current === item.id
              ? "border-l-primary bg-primary/12 text-foreground"
              : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
          )}
        >
          <span className="truncate">{item.label}</span>
          {item.dot ? (
            <span
              className="size-1.5 shrink-0 rounded-full bg-warn"
              aria-label="unsaved changes in this section"
            />
          ) : null}
          {item.meta ? (
            <span className="ml-auto font-mono text-[10px] text-faint-foreground">{item.meta}</span>
          ) : null}
        </a>
      ))}
    </nav>
  );
}
