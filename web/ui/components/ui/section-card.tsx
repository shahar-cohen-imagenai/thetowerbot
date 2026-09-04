import * as React from "react";

import { Card, CardAction, CardContent, CardHeader } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** Tints a 1px top edge, never the whole surface. A card that reports a state
 *  should read as itself with a state, not as a coloured block. */
const TONE = {
  live: "border-t-2 border-t-live",
  warn: "border-t-2 border-t-warn",
  danger: "border-t-2 border-t-danger",
} as const;

/**
 * The panel every page is built from.
 *
 * Replaces the `<section className="rounded-lg border p-3">` + uppercase `<h2>`
 * pair that was written out 23 times, and does it through the Card primitive
 * rather than beside it - `size="sm"` sets --card-spacing, so the padding is
 * not overridden back off the way `className="gap-3 p-3"` was doing.
 */
export function SectionCard({
  title,
  action,
  tone,
  className,
  contentClassName,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  title: React.ReactNode;
  /** Rendered at the far end of the header row - a count, a filter, a button. */
  action?: React.ReactNode;
  tone?: keyof typeof TONE;
  contentClassName?: string;
}) {
  return (
    // scroll-mt clears the sticky toolbar on /strategy: without it an anchor
    // jump from the section nav parks the card's first line underneath it.
    <Card size="sm" className={cn("scroll-mt-24", tone && TONE[tone], className)} {...props}>
      <CardHeader>
        {/* A real <h2>, not CardTitle's <div>: these panels are the page's
            section structure, and the pages this replaced were already using
            headings here. Losing them would take the whole document outline
            with it. */}
        <h2 className="font-heading text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          {title}
        </h2>
        {action ? <CardAction>{action}</CardAction> : null}
      </CardHeader>
      {/* Stacked with a gap by default. The Card this replaced laid its own
          children out with gap-3, so content that is several paragraphs and
          tables long (the Guide) would otherwise collapse into one block with
          Tailwind's margin reset and nothing to separate it. */}
      <CardContent className={cn("flex flex-col gap-3", contentClassName)}>
        {children}
      </CardContent>
    </Card>
  );
}
