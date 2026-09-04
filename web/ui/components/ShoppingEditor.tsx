"use client";

import { TriangleAlert } from "lucide-react";
import { type ComponentRef, useEffect, useRef, useState } from "react";
import { OrderChip, ReorderButtons } from "@/components/StrategyEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NumberField } from "@/components/ui/number-field";
import { SectionCard } from "@/components/ui/section-card";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import type { CardPolicy, Shopping, ShoppingRule } from "@/lib/types";

/** Verbatim from the Guide's "Per-row hints for the Strategy page" table,
 * keyed by row name. A row not in this map gets no hint rather than a made
 * up one. */
const HINTS: Record<string, string> = {
  "Unlock Cash Bonuses":
    "Opens the whole Utility tab — Cash Bonus and Coins/Kill live behind it. 40 coins.",
  "Unlock Defense Upgrades":
    "Opens Def Abs, Def% and Thorns, the core of a tier-1 turtle build. 75 coins.",
  "Unlock Range Upgrades": "Opens the rest of the Attack tab. 50 coins.",
  Health: "Community says a level or two early, not a lot — Def Abs does the heavy lifting.",
  "Health Regen": "Cheap, and it keeps a turtle build alive between waves.",
  Damage: "Wanted, but after economy and defence are working.",
  "Attack Speed": "More attacks means more chances for every on-hit effect to fire.",
  "Critical Chance":
    "Off by default: costs more and scales slower than everything above it early.",
  "Critical Factor": "Off by default, same reason as Critical Chance.",
};

/** The gem floor's warning copy. A test asserts on this exact phrase, so it
 * and the rendered text must stay in step. */
const GEM_FLOOR_NOTE = "Gems cannot be earned back quickly, unlike coins.";

const VISIT_FREQUENCY_NOTE =
  "How many runs pass between shopping visits; 1 means every run.";

const TAP_BUDGET_NOTE =
  "Bounds an errand that isn't making progress, so a mis-cut template can't spin " +
  "forever - exhausting it aborts the visit.";

export function ShoppingEditor({
  shopping,
  onChange,
  disabled = false,
  disabledReason = null,
}: {
  shopping: Shopping;
  onChange: (next: Shopping) => void;
  disabled?: boolean;
  /** Non-null when this machine's header glyph atlas cannot support a
   * balance read - ShoppingSession.begin() then declines every visit
   * forever, regardless of what is configured here. Surfaced so enabling
   * and arming the feature says why nothing happens instead of doing
   * nothing silently - see /api/control's shopping_disabled_reason. */
  disabledReason?: string | null;
}) {
  // Local, not lifted into `shopping`: this is UI-only intent to arm, not a
  // policy value. It must never survive a remount (a Revert, a tab switch)
  // half-confirmed.
  const [confirmingArm, setConfirmingArm] = useState(false);
  // The arm confirmation is the one control on this page that spends
  // currency a player cannot get back. Focusing Cancel when it opens - the
  // safe default, not the destructive button - is what gives a screen
  // reader user any signal at all that the arm switch just did something;
  // without it, the panel simply appears with no announcement of intent.
  const cancelRef = useRef<ComponentRef<typeof Button>>(null);
  useEffect(() => {
    if (confirmingArm) cancelRef.current?.focus();
  }, [confirmingArm]);

  const set = <K extends keyof Shopping>(key: K, v: Shopping[K]) =>
    onChange({ ...shopping, [key]: v });

  const setRow = (index: number, patch: Partial<ShoppingRule>) =>
    set(
      "workshop",
      shopping.workshop.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );

  const move = (index: number, delta: number) => {
    const next = [...shopping.workshop];
    const target = index + delta;
    // Swap, don't splice-and-insert - same reasoning as StrategyEditor's move.
    [next[index], next[target]] = [next[target], next[index]];
    set("workshop", next);
  };

  const setCards = (patch: Partial<CardPolicy>) => set("cards", { ...shopping.cards, ...patch });

  // Arming needs an explicit second step because it is the only control in
  // the dashboard that spends something the player cannot get back;
  // disarming is the safe direction and happens immediately.
  const toggleArm = () => {
    if (shopping.armed) {
      set("armed", false);
      return;
    }
    setConfirmingArm(true);
  };

  const enabledRows = shopping.workshop.filter((row) => row.enabled).length;

  return (
    <div className="flex flex-col gap-4">
      <SectionCard
        id="shopping"
        title="Shopping"
        tone={shopping.armed ? "danger" : undefined}
        action={
          <div className="flex items-center gap-2 text-sm">
            <span className="text-xs text-muted-foreground">Shop between runs</span>
            <Switch
              label="Shopping enabled"
              checked={shopping.enabled}
              disabled={disabled}
              onCheckedChange={(next) => set("enabled", next)}
            />
          </div>
        }
        contentClassName="flex flex-col gap-3"
      >
        {/* This is the reason the entire feature is inert. It was rendered as
            a dashed muted paragraph, which reads as a footnote. */}
        {disabledReason ? (
          <div className="flex gap-2 rounded-md border border-warn bg-warn-surface p-2.5">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden="true" />
            <p className="text-xs text-warn">
              Shopping cannot run on this machine yet: {disabledReason}. Enabling or arming it
              below will not do anything until this is fixed.
            </p>
          </div>
        ) : null}

        {/* The arm block leads the card now: it is the only control here that
            spends currency nobody gets back, so it should not be the fourth
            thing you meet on the way down. */}
        <div
          className={cn(
            "flex flex-wrap items-center justify-between gap-3 rounded-md border-2 p-3",
            shopping.armed
              ? "border-danger bg-danger-surface"
              : "border-dashed border-border-strong",
          )}
        >
          <div className="flex flex-col gap-1 text-sm">
            {/* The heading itself carries the state - promoted in weight and
                colour rather than duplicated into a separate badge, so the
                phrase a reader searches for still appears exactly once. */}
            <span
              className={cn(
                "font-semibold",
                shopping.armed ? "uppercase tracking-[0.06em] text-danger" : "text-foreground",
              )}
            >
              {!shopping.enabled
                ? "Shopping is off"
                : shopping.armed
                  ? "Armed — spends real coins and gems"
                  : "Unarmed — rehearsal"}
            </span>
            <span className="max-w-md text-xs text-muted-foreground">
              {/* Must stay true to ShoppingSession.begin(), which declines
                  outright when `enabled` is false - no visit happens at all,
                  so the rehearsal/armed copy below would be a lie in that
                  state. `armed` only changes what happens WITHIN a visit, so
                  it only gets to speak once `enabled` says a visit occurs. */}
              {!shopping.enabled
                ? "No shopping visits happen at all until \"Shop between runs\" is turned on."
                : shopping.armed
                  ? "Every purchase below is actually tapped on the bot's next shopping visit."
                  : "The bot still navigates to the shop, reads the balances, decides what it " +
                    "would buy, and reports every purchase it would make - it just taps nothing."}
            </span>
          </div>
          <Switch
            label="Arm shopping (spends real coins and gems)"
            checked={shopping.armed}
            disabled={disabled}
            onCheckedChange={toggleArm}
            size="lg"
            tone="danger"
          />
        </div>

        {confirmingArm ? (
          // Not a full modal - that would be over-engineering for this
          // page's idiom, where every other confirmation (Delete, Discard
          // unsaved changes) is a plain inline panel or window.confirm. But
          // this is the one control that spends currency nobody gets back,
          // so it earns alertdialog + assertive plus the focus move below:
          // together they are what tells a screen-reader user the arm
          // switch did anything at all, rather than the panel just
          // silently appearing.
          <div
            role="alertdialog"
            aria-live="assertive"
            aria-label="Confirm arming shopping"
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border-2 border-danger bg-danger-surface p-2.5 text-sm"
          >
            <span>
              Arming spends real coins and gems on the bot&apos;s next shopping visit. This
              cannot be undone.
            </span>
            <div className="flex gap-2">
              <Button
                ref={cancelRef}
                type="button" variant="outline" size="sm" disabled={disabled}
                onClick={() => setConfirmingArm(false)}
              >
                Cancel
              </Button>
              <Button
                type="button" variant="danger" size="sm" disabled={disabled}
                onClick={() => {
                  setConfirmingArm(false);
                  set("armed", true);
                }}
              >
                Yes, spend coins
              </Button>
            </div>
          </div>
        ) : null}

        <div className="flex flex-col gap-2 rounded-md border p-2.5">
          <NumberField
            label="Visit frequency" ariaLabel="Visit every N runs"
            note={VISIT_FREQUENCY_NOTE}
            value={shopping.visit_every_n_runs} disabled={disabled}
            min={1} max={100} step={1} width="w-20"
            onCommit={(n) => set("visit_every_n_runs", n)}
          />
          <NumberField
            label="Tap budget per visit" ariaLabel="Max taps per visit"
            note={TAP_BUDGET_NOTE}
            value={shopping.max_taps_per_visit} disabled={disabled}
            min={1} max={200} step={1} width="w-20"
            onCommit={(n) => set("max_taps_per_visit", n)}
          />
        </div>

        <div className="flex items-baseline justify-between">
          <p className="text-xs text-muted-foreground">
            Visited tab by tab in this order, top to bottom within a tab — the order is the buy
            priority.
          </p>
          <span className="shrink-0 pl-2 font-mono text-xs text-muted-foreground">
            {enabledRows}/{shopping.workshop.length} on
          </span>
        </div>

        <div className="flex flex-col gap-2">
          {shopping.workshop.map((row, index) => {
            const hint = HINTS[row.name];
            return (
              <div
                key={row.name}
                data-testid="shopping-row"
                data-name={row.name}
                className={cn(
                  "flex flex-col gap-1 rounded-md border p-2 text-sm",
                  !row.enabled && "opacity-60",
                )}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <OrderChip index={index} enabled={row.enabled} />
                  <Switch
                    label="Enabled"
                    checked={row.enabled}
                    disabled={disabled}
                    onCheckedChange={(next) => setRow(index, { enabled: next })}
                  />
                  <span className={cn("min-w-32 flex-1 font-medium", !row.enabled && "text-muted-foreground")}>
                    {row.name}
                  </span>
                  <Badge variant="outline" className="font-mono text-[10px] uppercase">
                    {row.category}
                  </Badge>
                  {/* Read-only: `layout` records which workshop layout this
                      row's template was cut for, which decides where the bot
                      reads the price. Editing it here could not change where
                      the game actually renders that price - it would only make
                      the bot read the wrong pixels and report a wrong number
                      instead of a refused one. config.WORKSHOP_ROWS enforces
                      this server-side too. */}
                  <Badge
                    variant="outline"
                    className="font-mono text-[10px] uppercase"
                    title="Which workshop layout this row's template was cut for - read-only, set when the template was measured"
                  >
                    layout: {row.layout}
                  </Badge>
                  <label className="flex items-center gap-1 text-xs text-muted-foreground">
                    match
                    <Input
                      type="number" min={0.05} max={1} step={0.05}
                      aria-label={`${row.name} threshold`}
                      key={row.threshold}
                      defaultValue={row.threshold}
                      disabled={disabled}
                      onBlur={(e) => setRow(index, { threshold: Number(e.target.value) })}
                      className="w-20 text-right font-mono"
                    />
                  </label>
                  {/* No brightness control here on purpose: shopping.py has no
                      brightness path for menu prices at all (the game
                      desaturates rather than dims an unaffordable button on
                      these pages - see the Guide and shopping.py's module
                      docstring), so an editable field here would let someone
                      tune a knob that is never read. brightness_ratio stays on
                      ShoppingRule for schema symmetry with ActionRule, not
                      because this page can do anything with it. */}
                  <ReorderButtons
                    index={index} count={shopping.workshop.length} disabled={disabled}
                    onMove={(delta) => move(index, delta)}
                  />
                </div>
                {hint ? (
                  <p data-testid={`hint-${row.name}`} className="pl-7 text-xs text-muted-foreground">
                    {hint}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      </SectionCard>

      <SectionCard id="cards" title="Cards" contentClassName="flex flex-col gap-3">
        <div className="flex items-center justify-between text-sm">
          Buy cards with gems
          <Switch
            label="Cards enabled"
            checked={shopping.cards.enabled}
            disabled={disabled}
            onCheckedChange={(next) => setCards({ enabled: next })}
          />
        </div>
        <NumberField
          label="Gem floor"
          note={`${GEM_FLOOR_NOTE} The bot stops buying cards once spending would take the balance below this.`}
          value={shopping.cards.gem_floor} disabled={disabled}
          min={0} step={1}
          onCommit={(n) => setCards({ gem_floor: n })}
        />
        <NumberField
          label="Max cards per visit"
          value={shopping.cards.max_per_visit} disabled={disabled}
          min={1} step={1}
          onCommit={(n) => setCards({ max_per_visit: n })}
        />
        <div className="text-sm">
          <div className="mb-1">Batch size</div>
          {(["x1", "x10"] as const).map((b) => (
            <label key={b} className="mr-4">
              <input
                type="radio" name="card-batch" aria-label={b}
                className="accent-primary"
                checked={shopping.cards.batch === b}
                disabled={disabled}
                onChange={() => setCards({ batch: b })}
              />{" "}
              {b}
            </label>
          ))}
        </div>
      </SectionCard>
    </div>
  );
}
