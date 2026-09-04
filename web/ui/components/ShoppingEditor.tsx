"use client";

import { type ComponentRef, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { CardPolicy, Shopping, ShoppingRule } from "@/lib/types";

/** Verbatim from the Guide's "Per-row hints for the Strategy page" table,
 * keyed by row name. A row not in this map gets no hint rather than a made
 * up one - see task-12-overrides.md, Override 2. */
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
 * and the rendered text must stay in step - see task-12-overrides.md,
 * Override 3. */
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
  // disarming is the safe direction and happens immediately. See
  // task-12-overrides.md, Override 3.
  const toggleArm = () => {
    if (shopping.armed) {
      set("armed", false);
      return;
    }
    setConfirmingArm(true);
  };

  return (
    <div className="flex flex-col gap-4">
      <Card className="gap-3 p-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs uppercase tracking-wide text-muted-foreground">Shopping</h2>
          <label className="flex items-center gap-2 text-sm">
            Shop between runs
            <input
              type="checkbox"
              aria-label="Shopping enabled"
              checked={shopping.enabled}
              disabled={disabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
          </label>
        </div>

        {disabledReason ? (
          <p className="rounded-md border border-dashed p-2 text-xs text-muted-foreground">
            Shopping cannot run on this machine yet: {disabledReason}. Enabling or arming it
            below will not do anything until this is fixed.
          </p>
        ) : null}

        <div className="flex flex-col gap-2 rounded-md border p-2 text-sm">
          <label className="flex items-center justify-between gap-2">
            <span>
              Visit frequency
              <span className="block max-w-xs text-xs text-muted-foreground">
                {VISIT_FREQUENCY_NOTE}
              </span>
            </span>
            <Input
              type="number" min={1} max={100} step={1}
              aria-label="Visit every N runs"
              key={shopping.visit_every_n_runs}
              defaultValue={shopping.visit_every_n_runs}
              disabled={disabled}
              onBlur={(e) => set("visit_every_n_runs", Number(e.target.value))}
              className="w-20 text-right"
            />
          </label>
          <label className="flex items-center justify-between gap-2">
            <span>
              Tap budget per visit
              <span className="block max-w-xs text-xs text-muted-foreground">
                {TAP_BUDGET_NOTE}
              </span>
            </span>
            <Input
              type="number" min={1} max={200} step={1}
              aria-label="Max taps per visit"
              key={shopping.max_taps_per_visit}
              defaultValue={shopping.max_taps_per_visit}
              disabled={disabled}
              onBlur={(e) => set("max_taps_per_visit", Number(e.target.value))}
              className="w-20 text-right"
            />
          </label>
        </div>

        {/* The arm switch: deliberately not styled like the checkboxes on
            this page. It is the one control here that spends real currency,
            so it must not be mistaken for one of them at a glance. */}
        <div
          className={`flex flex-wrap items-center justify-between gap-3 rounded-md border-2 p-3 ${
            shopping.armed
              ? "border-destructive bg-destructive/10"
              : "border-dashed border-muted-foreground/40"
          }`}
        >
          <div className="flex flex-col gap-1 text-sm">
            <span className="font-semibold">
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
          <button
            type="button"
            role="switch"
            aria-checked={shopping.armed}
            aria-label="Arm shopping (spends real coins and gems)"
            disabled={disabled}
            onClick={toggleArm}
            className={`relative h-7 w-14 shrink-0 rounded-full border-2 transition-colors disabled:opacity-50 ${
              shopping.armed
                ? "border-destructive bg-destructive"
                : "border-muted-foreground/50 bg-muted"
            }`}
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-background shadow transition-transform ${
                shopping.armed ? "translate-x-7" : "translate-x-0.5"
              }`}
            />
          </button>
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
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive bg-destructive/10 p-2 text-sm"
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
                type="button" variant="destructive" size="sm" disabled={disabled}
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

        <p className="text-xs text-muted-foreground">
          Visited tab by tab in this order, top to bottom within a tab — the order is the buy
          priority.
        </p>

        {shopping.workshop.map((row, index) => {
          const hint = HINTS[row.name];
          return (
            <div
              key={row.name}
              data-testid="shopping-row"
              data-name={row.name}
              className="flex flex-col gap-1 rounded-md border p-2 text-sm"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-4 text-center text-xs text-muted-foreground">{index + 1}</span>
                <input
                  type="checkbox"
                  aria-label="Enabled"
                  checked={row.enabled}
                  disabled={disabled}
                  onChange={(e) => setRow(index, { enabled: e.target.checked })}
                />
                <span className="min-w-32 flex-1 font-medium">{row.name}</span>
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {row.category}
                </span>
                {/* Read-only: `layout` records which workshop layout this
                    row's template was cut for, which decides where the bot
                    reads the price. Editing it here could not change where
                    the game actually renders that price - it would only make
                    the bot read the wrong pixels and report a wrong number
                    instead of a refused one. config.WORKSHOP_ROWS enforces
                    this server-side too. */}
                <span
                  className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
                  title="Which workshop layout this row's template was cut for - read-only, set when the template was measured"
                >
                  layout: {row.layout}
                </span>
                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                  match
                  <Input
                    type="number" min={0.05} max={1} step={0.05}
                    aria-label={`${row.name} threshold`}
                    key={row.threshold}
                    defaultValue={row.threshold}
                    disabled={disabled}
                    onBlur={(e) => setRow(index, { threshold: Number(e.target.value) })}
                    className="w-20 text-right"
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
                <button
                  type="button" aria-label="Move up"
                  disabled={disabled || index === 0}
                  onClick={() => move(index, -1)}
                  className="rounded border px-2 disabled:opacity-30"
                >
                  ↑
                </button>
                <button
                  type="button" aria-label="Move down"
                  disabled={disabled || index === shopping.workshop.length - 1}
                  onClick={() => move(index, 1)}
                  className="rounded border px-2 disabled:opacity-30"
                >
                  ↓
                </button>
              </div>
              {hint ? (
                <p data-testid={`hint-${row.name}`} className="text-xs text-muted-foreground">
                  {hint}
                </p>
              ) : null}
            </div>
          );
        })}
      </Card>

      <Card className="gap-3 p-3">
        <h2 className="text-xs uppercase tracking-wide text-muted-foreground">Cards</h2>
        <label className="flex items-center justify-between text-sm">
          Buy cards with gems
          <input
            type="checkbox"
            aria-label="Cards enabled"
            checked={shopping.cards.enabled}
            disabled={disabled}
            onChange={(e) => setCards({ enabled: e.target.checked })}
          />
        </label>
        <label className="flex items-center justify-between text-sm">
          <span>
            Gem floor
            <span className="block max-w-xs text-xs text-muted-foreground">
              {GEM_FLOOR_NOTE} The bot stops buying cards once spending would take the balance
              below this.
            </span>
          </span>
          <Input
            type="number" min={0} step={1} aria-label="Gem floor"
            key={shopping.cards.gem_floor}
            defaultValue={shopping.cards.gem_floor}
            disabled={disabled}
            onBlur={(e) => setCards({ gem_floor: Number(e.target.value) })}
            className="w-24 text-right"
          />
        </label>
        <label className="flex items-center justify-between text-sm">
          Max cards per visit
          <Input
            type="number" min={1} step={1} aria-label="Max cards per visit"
            key={shopping.cards.max_per_visit}
            defaultValue={shopping.cards.max_per_visit}
            disabled={disabled}
            onBlur={(e) => setCards({ max_per_visit: Number(e.target.value) })}
            className="w-24 text-right"
          />
        </label>
        <div className="text-sm">
          <div className="mb-1">Batch size</div>
          {(["x1", "x10"] as const).map((b) => (
            <label key={b} className="mr-4">
              <input
                type="radio" name="card-batch" aria-label={b}
                checked={shopping.cards.batch === b}
                disabled={disabled}
                onChange={() => setCards({ batch: b })}
              />{" "}
              {b}
            </label>
          ))}
        </div>
      </Card>
    </div>
  );
}
