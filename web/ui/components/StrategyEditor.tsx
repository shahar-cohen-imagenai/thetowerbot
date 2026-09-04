"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NumberField } from "@/components/ui/number-field";
import { SectionCard } from "@/components/ui/section-card";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import type { ActionRule, Strategy } from "@/lib/types";

/** Fields whose value is read when the bot is BUILT, not per scan.
 *
 * screen_confirmations is ScreenTracker's debounce depth and
 * navigation_cooldown is Navigator's rate limit; both objects carry state
 * across scans, so changing them under a running one has no correct answer.
 * Saying so on the page is the difference between a documented boundary and
 * a control that silently does nothing. */
const START_ONLY = "applies on next Start";

function StartOnly() {
  return (
    <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
      {START_ONLY}
    </span>
  );
}

/** Shared by both editors' rule lists. Priority ordering is a core mechanic of
 *  this page and was driven by the smallest hit targets on it - roughly
 *  24x20px arrows made of text glyphs. */
export function ReorderButtons({
  index, count, disabled, onMove,
}: {
  index: number;
  count: number;
  disabled?: boolean;
  onMove: (delta: number) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <Button
        type="button" variant="ghost" size="icon-sm" aria-label="Move up"
        disabled={disabled || index === 0}
        onClick={() => onMove(-1)}
      >
        <ChevronUp />
      </Button>
      <Button
        type="button" variant="ghost" size="icon-sm" aria-label="Move down"
        disabled={disabled || index === count - 1}
        onClick={() => onMove(1)}
      >
        <ChevronDown />
      </Button>
    </div>
  );
}

/** The order badge. Its text is the priority, and the priority is the point. */
export function OrderChip({ index, enabled }: { index: number; enabled: boolean }) {
  return (
    <span
      className={cn(
        "w-5 shrink-0 rounded text-center font-mono text-[11px] leading-5",
        enabled ? "bg-muted text-foreground" : "text-faint-foreground",
      )}
    >
      {index + 1}
    </span>
  );
}

export function StrategyEditor({
  value,
  onChange,
  available,
  disabled = false,
}: {
  value: Strategy;
  onChange: (next: Strategy) => void;
  available?: string[];
  disabled?: boolean;
}) {
  const set = <K extends keyof Strategy>(key: K, v: Strategy[K]) =>
    onChange({ ...value, [key]: v });

  const setRow = (index: number, patch: Partial<ActionRule>) =>
    set(
      "actions",
      value.actions.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );

  const move = (index: number, delta: number) => {
    const next = [...value.actions];
    const target = index + delta;
    // Swap, don't splice-and-insert: with two adjacent rows they are the
    // same thing, and a swap cannot drop a row if the index is ever wrong.
    [next[index], next[target]] = [next[target], next[index]];
    set("actions", next);
  };

  const usable = (name: string) => available === undefined || available.includes(name);
  const enabledCount = value.actions.filter((row) => row.enabled).length;

  return (
    <div className="flex flex-col gap-4">
      <SectionCard
        id="purchases"
        title="Purchases"
        action={
          <span className="font-mono text-xs text-muted-foreground">
            {enabledCount}/{value.actions.length} on
          </span>
        }
      >
        <p className="mb-3 text-xs text-muted-foreground">
          Evaluated top to bottom on every scan — the order is the priority.
        </p>
        <div className="flex flex-col gap-2">
          {value.actions.map((row, index) => (
            <div
              key={row.name}
              data-testid="action-row"
              data-name={row.name}
              // A disabled rule used to be visually identical to an enabled
              // one but for the state of a 13px checkbox.
              className={cn(
                "flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm",
                !row.enabled && "opacity-60",
              )}
            >
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
              {!row.enabled ? (
                <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide text-faint-foreground">
                  off
                </span>
              ) : null}
              <label className="flex items-center gap-1 text-xs text-muted-foreground">
                match
                <Input
                  type="number" min={0.05} max={1} step={0.05}
                  aria-label={`${row.name} threshold`}
                  // Keyed on the value for the same reason NumberField is:
                  // defaultValue is only honoured at mount, and this row's
                  // own key={row.name} does not change when row.threshold
                  // does, so without this a Revert would update the draft but
                  // leave the field showing the stale, previously-typed number.
                  key={row.threshold}
                  defaultValue={row.threshold}
                  disabled={disabled}
                  onBlur={(e) => setRow(index, { threshold: Number(e.target.value) })}
                  className="w-20 text-right font-mono"
                />
              </label>
              <label className="flex items-center gap-1 text-xs text-muted-foreground">
                brightness
                <Input
                  type="number" min={0} max={1} step={0.05}
                  aria-label={`${row.name} brightness`}
                  // Same reasoning as the threshold field's key above.
                  key={row.brightness_ratio}
                  defaultValue={row.brightness_ratio}
                  disabled={disabled}
                  onBlur={(e) =>
                    setRow(index, { brightness_ratio: Number(e.target.value) })
                  }
                  className="w-20 text-right font-mono"
                />
              </label>
              <ReorderButtons
                index={index} count={value.actions.length} disabled={disabled}
                onMove={(delta) => move(index, delta)}
              />
            </div>
          ))}
        </div>

        <div className="mt-3 text-sm">
          <div className="mb-1">Affordability</div>
          {["digits", "brightness"].map((name) => (
            <label
              key={name}
              className={`mr-4 ${usable(name) ? "" : "text-muted-foreground"}`}
            >
              <input
                type="radio" name="affordability" aria-label={name}
                className="accent-primary"
                checked={value.affordability === name}
                disabled={disabled || !usable(name)}
                onChange={() => set("affordability", name)}
              />{" "}
              {name}
              {usable(name) ? "" : " (no atlas)"}
            </label>
          ))}
        </div>
      </SectionCard>

      <SectionCard id="timing" title="Timing" contentClassName="flex flex-col gap-3">
        <NumberField
          label="Scan interval (s)" value={value.interval} disabled={disabled}
          min={0.1} max={3600} step={0.1}
          onCommit={(n) => set("interval", n)}
        />
        <NumberField
          label="Click cooldown (s)" value={value.click_cooldown} disabled={disabled}
          min={0} max={60} step={0.1}
          onCommit={(n) => set("click_cooldown", n)}
        />
        <NumberField
          label="Navigation cooldown (s)" value={value.navigation_cooldown}
          disabled={disabled} min={0} max={60} step={0.1}
          onCommit={(n) => set("navigation_cooldown", n)} badge={<StartOnly />}
        />
        <NumberField
          label="Screen confirmations" value={value.screen_confirmations}
          disabled={disabled} min={1} max={10} step={1}
          onCommit={(n) => set("screen_confirmations", n)} badge={<StartOnly />}
        />
      </SectionCard>

      <SectionCard id="jitter" title="Jitter" contentClassName="flex flex-col gap-3">
        <p className="text-xs text-muted-foreground">
          Every tap is an ADB <code>input tap</code>: no travel, no dwell, and the
          same pixel every time. These scatter it. Zero switches each one off.
        </p>
        <NumberField
          label="Tap jitter (px)" value={value.tap_jitter_px} disabled={disabled}
          min={0} max={19} step={0.5}
          onCommit={(n) => set("tap_jitter_px", n)}
        />
        <NumberField
          label="Timing jitter (fraction)" value={value.timing_jitter}
          disabled={disabled} min={0} max={0.5} step={0.05}
          onCommit={(n) => set("timing_jitter", n)}
        />
        <NumberField
          label="Tap delay (s)" value={value.tap_delay} disabled={disabled}
          min={0} max={2} step={0.05}
          onCommit={(n) => set("tap_delay", n)}
        />
      </SectionCard>

      <SectionCard id="run-policy" title="Run policy" contentClassName="flex flex-col gap-3">
        <div className="flex items-center justify-between text-sm">
          Auto-navigate
          <Switch
            label="Auto-navigate"
            checked={value.auto_navigate}
            disabled={disabled}
            onCheckedChange={(next) => set("auto_navigate", next)}
          />
        </div>
        <label className="flex items-center justify-between text-sm">
          <span>
            Max runs{" "}
            <span className="text-xs text-muted-foreground">
              {value.max_runs === null ? "(unlimited)" : ""}
            </span>
          </span>
          <Input
            type="number" min={1} step={1} aria-label="Max runs"
            // An empty field IS null here - the only way to express
            // "unlimited" in a number input, and why the page saves the whole
            // profile rather than PATCHing (PATCH cannot carry a null).
            // Keyed on the value for the same reason as the fields above -
            // otherwise a Revert to null would leave a previously-typed
            // number sitting on screen.
            key={value.max_runs}
            defaultValue={value.max_runs ?? ""}
            disabled={disabled}
            onBlur={(e) =>
              set("max_runs", e.target.value === "" ? null : Number(e.target.value))
            }
            className="w-24 text-right font-mono"
          />
        </label>
      </SectionCard>
    </div>
  );
}
