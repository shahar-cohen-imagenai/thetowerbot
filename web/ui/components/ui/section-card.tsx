import * as React from "react";

import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
        <CardTitle className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          {title}
        </CardTitle>
        {action ? <CardAction>{action}</CardAction> : null}
      </CardHeader>
      <CardContent className={contentClassName}>{children}</CardContent>
    </Card>
  );
}
