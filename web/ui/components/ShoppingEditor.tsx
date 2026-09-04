"use client";

import { useState } from "react";
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

export function ShoppingEditor({
  shopping,
  onChange,
  disabled = false,
}: {
  shopping: Shopping;
  onChange: (next: Shopping) => void;
  disabled?: boolean;
}) {
  // Local, not lifted into `shopping`: this is UI-only intent to arm, not a
  // policy value. It must never survive a remount (a Revert, a tab switch)
  // half-confirmed.
  const [confirmingArm, setConfirmingArm] = useState(false);

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
            Between-run visits
            <input
              type="checkbox"
              aria-label="Shopping enabled"
              checked={shopping.enabled}
              disabled={disabled}
              onChange={(e) => set("enabled", e.target.checked)}
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
              {shopping.armed ? "Armed — spends real coins and gems" : "Unarmed — rehearsal"}
            </span>
            <span className="max-w-md text-xs text-muted-foreground">
              {shopping.armed
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
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive bg-destructive/10 p-2 text-sm">
            <span>
              Arming spends real coins and gems on the bot&apos;s next shopping visit. This
              cannot be undone.
            </span>
            <div className="flex gap-2">
              <Button
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
                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                  brightness
                  <Input
                    type="number" min={0} max={1} step={0.05}
                    aria-label={`${row.name} brightness`}
                    key={row.brightness_ratio}
                    defaultValue={row.brightness_ratio}
                    disabled={disabled}
                    onBlur={(e) =>
                      setRow(index, { brightness_ratio: Number(e.target.value) })
                    }
                    className="w-20 text-right"
                  />
                </label>
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
                  {/* Visually-hidden name prefix: the guide's hint text
                      stands on its own next to the row, but a hint element
                      read out of context (e.g. by a screen reader jumping
                      straight to it) should still say which row it explains. */}
                  <span className="sr-only">{row.name} — </span>
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
