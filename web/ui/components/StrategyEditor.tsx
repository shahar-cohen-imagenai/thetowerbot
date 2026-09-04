"use client";

import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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

  return (
    <div className="flex flex-col gap-4">
      <Card className="gap-3 p-3">
        <h2 className="text-xs uppercase tracking-wide text-muted-foreground">
          Purchases
        </h2>
        <p className="text-xs text-muted-foreground">
          Evaluated top to bottom on every scan — the order is the priority.
        </p>
        {value.actions.map((row, index) => (
          <div
            key={row.name}
            data-testid="action-row"
            data-name={row.name}
            className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm"
          >
            <span className="w-4 text-center text-xs text-muted-foreground">
              {index + 1}
            </span>
            <input
              type="checkbox"
              aria-label="Enabled"
              checked={row.enabled}
              disabled={disabled}
              onChange={(e) => setRow(index, { enabled: e.target.checked })}
            />
            <span className="min-w-32 flex-1 font-medium">{row.name}</span>
            <label className="flex items-center gap-1 text-xs text-muted-foreground">
              match
              <Input
                type="number" min={0.05} max={1} step={0.05}
                aria-label={`${row.name} threshold`}
                // Keyed on the value for the same reason NumberField below
                // is: defaultValue is only honoured at mount, and this row's
                // own key={row.name} does not change when row.threshold
                // does, so without this a Revert would update the draft but
                // leave the field showing the stale, previously-typed number.
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
                // Same reasoning as the threshold field's key above.
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
              disabled={disabled || index === value.actions.length - 1}
              onClick={() => move(index, 1)}
              className="rounded border px-2 disabled:opacity-30"
            >
              ↓
            </button>
          </div>
        ))}

        <div className="text-sm">
          <div className="mb-1">Affordability</div>
          {["digits", "brightness"].map((name) => (
            <label
              key={name}
              className={`mr-4 ${usable(name) ? "" : "text-muted-foreground"}`}
            >
              <input
                type="radio" name="affordability" aria-label={name}
                checked={value.affordability === name}
                disabled={disabled || !usable(name)}
                onChange={() => set("affordability", name)}
              />{" "}
              {name}
              {usable(name) ? "" : " (no atlas)"}
            </label>
          ))}
        </div>
      </Card>

      <Card className="gap-3 p-3">
        <h2 className="text-xs uppercase tracking-wide text-muted-foreground">Timing</h2>
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
      </Card>

      <Card className="gap-3 p-3">
        <h2 className="text-xs uppercase tracking-wide text-muted-foreground">Jitter</h2>
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
      </Card>

      <Card className="gap-3 p-3">
        <h2 className="text-xs uppercase tracking-wide text-muted-foreground">
          Run policy
        </h2>
        <label className="flex items-center justify-between text-sm">
          Auto-navigate
          <input
            type="checkbox" checked={value.auto_navigate} disabled={disabled}
            onChange={(e) => set("auto_navigate", e.target.checked)}
          />
        </label>
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
            className="w-24 text-right"
          />
        </label>
      </Card>
    </div>
  );
}

function NumberField({
  label, value, onCommit, min, max, step, disabled, badge,
}: {
  label: string;
  value: number;
  onCommit: (n: number) => void;
  min: number; max: number; step: number;
  disabled?: boolean;
  badge?: React.ReactNode;
}) {
  return (
    <label className="flex items-center justify-between text-sm">
      <span>
        {label}
        {badge}
      </span>
      <Input
        type="number" min={min} max={max} step={step} aria-label={label}
        // Keyed on the value so a change from another tab (or a rejected
        // save) remounts the field rather than leaving a stale local edit
        // sitting there looking live - the same reasoning the old control
        // page documented for its interval input.
        key={value}
        defaultValue={value}
        disabled={disabled}
        onBlur={(e) => onCommit(Number(e.target.value))}
        className="w-24 text-right"
      />
    </label>
  );
}
