# The Strategy page and the reshaped Control page — Implementation Plan (4 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Strategy page that shows and edits the whole decision policy, a Control page reduced to session concerns, and a running/stopped pill in the status bar.

**Architecture:** The two pages split along the line the model already draws — `Controls` holds session state, the `Strategy` holds policy, so Control owns Start/Stop/pause and Strategy owns everything else. Both converge across tabs over the existing SSE feed, using the `ControlChanged` listener the current control page already implements. No new push mechanism and no new dependency: reordering is up/down buttons, not drag-and-drop.

**Tech Stack:** Next.js (static export), React, TypeScript, Tailwind, shadcn/ui primitives, vitest.

**Spec:** `docs/superpowers/specs/2026-09-02-bot-lifecycle-and-strategy-design.md` (section 9)

**Depends on:** plans 1-3. Every route this plan calls must already exist and be tested.

> **What plan 3's final review says this plan will trip on.** Read these before writing a component; each is a real property of the API you are building against.
>
> 1. **`/api/control` returns two different 422 shapes.** A `ControlError` becomes `detail: "interval: interval must be between…"` — a string with the field as a `": "`-delimited prefix. A pydantic type failure on the *same* endpoint becomes `detail: [{loc, msg, type}, …]` — a list. `web/ui/lib/api.ts`'s existing `describeDetail` already flattens both; use it rather than re-deriving. If a task wants to highlight the offending row or input rather than just show a message, say so — the honest fix is adding `{"field": exc.field}` to the server's response, not splitting strings in TypeScript.
> 2. **No optimistic concurrency anywhere.** `PUT /api/strategies/{name}` and `PATCH /api/control` are both last-write-wins over the whole document, and reorder is a whole-`actions` PUT — so two tabs on the Strategy page silently clobber each other, and the loser loses every row. The pages converge over SSE, which narrows the window but does not close it. Do not design as though it is closed.
> 3. **There is no restart endpoint.** A Restart button is `stop` then `start`, and another tab — or the bot hitting `max_runs` — can land in between, turning the second call into a 409. Treat 409 from `/api/bot/start` as "already running, refresh", not an error toast.
> 4. **`bot.error` is sticky and has no clear.** The runner clears it only on a *successful* start, and `stop()` returns it too — so after a failed Start the dashboard shows that error indefinitely, through stops and page loads. Render it as "last start failed", never as current state.
> 5. **`{running: false, since: null, error: null}` is identical for "never started" and "stopped cleanly".** A pill cannot distinguish idle from finished from this payload. Do not invent a distinction the API cannot support; if one is wanted, it needs a new field server-side.
> 6. **`bot.since` is wall-clock (`time.time()`), while `BotState`'s uptime is monotonic-derived.** The StatBar must not mix them in one calculation.
> 7. **`PATCH /api/control` cannot clear `max_runs`** — `exclude_none` makes absent and explicit-null the same request. The Strategy page must clear it by PUTting the whole profile, which is what it already does; do not add a `PATCH {"max_runs": null}` path expecting it to work.
>
> Also: `web/ui/lib/api.ts`'s `stopBot()` was repointed at `/api/shutdown` during plan 3, and `app/control/page.test.tsx` updated with it. Task 1 renames `stopBot` to mean `/api/bot/stop` — check what is there now rather than assuming the pre-plan-3 shape.

## Global Constraints

- The dashboard is a **static export**. No server components, no route handlers — `"use client"` at the top of every page, and every path in `Sidebar` keeps its trailing slash (`/strategy/`), which is what `next.config.ts`'s `trailingSlash: true` produces and what `StaticFiles(html=True)` resolves.
- Rebuild before the Python suite: `npm run build` in `web/ui`, and commit the regenerated `web/static/`. `tests/test_web_build.py` asserts against that committed output.
- Run frontend tests with `npm test` in `web/ui`. Run Python tests with `uv run pytest <file> -p no:allure_pytest`.
- Every write goes through the server and renders what the server returned — never an optimistic local update. The existing control page's `send()` is the pattern; keep it.
- No new npm dependencies.

---

## File Structure

| File | Responsibility |
|---|---|
| `web/ui/lib/api.ts` (modify) | the new endpoints' fetchers |
| `web/ui/lib/types.ts` (modify) | `StrategyList`, `BotStatus`; `StatusPayload.bot` |
| `web/ui/components/StrategyEditor.tsx` (create) | the form: rows, timing, run policy |
| `web/ui/components/ProfileBar.tsx` (create) | select / activate / duplicate / delete |
| `web/ui/app/strategy/page.tsx` (create) | composes the two, owns the fetching |
| `web/ui/app/control/page.tsx` (rewrite) | Start / Stop / Shut down / pause only |
| `web/ui/components/StatBar.tsx` (modify) | the running/stopped pill |
| `web/ui/components/Sidebar.tsx` (modify) | the sixth link |
| `web/ui/lib/useControlSync.ts` (create) | the ControlChanged listener, extracted |
| tests alongside each | vitest |

The `ControlChanged` listener is extracted to a hook because both pages need it and it is the subtlest code on either — the high-water-seq logic and its session-reset case are documented at length in the current control page, and duplicating that comment block into a second file is how the two copies start to drift.

---

### Task 1: API surface and the shared sync hook

**Files:**
- Modify: `web/ui/lib/types.ts`, `web/ui/lib/api.ts`
- Create: `web/ui/lib/useControlSync.ts`, `web/ui/lib/useControlSync.test.ts`

**Interfaces:**
- Produces:
  - `types.BotStatus = { running: boolean; since: number | null; error: string | null }`
  - `types.StrategyList = { active: string; names: string[] }`
  - `StatusPayload.bot: BotStatus`
  - `api.fetchStrategies()`, `api.fetchStrategy(name)`, `api.saveStrategy(name, body)`, `api.activateStrategy(name)`, `api.deleteStrategy(name)`, `api.startBot()`, `api.stopBot()`, `api.shutdown()`
  - `useControlSync(onChange: () => void): void`

- [ ] **Step 1: Write the failing test for the hook**

Create `web/ui/lib/useControlSync.test.ts`:

```typescript
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { shouldResync } from "./useControlSync";

/** shouldResync is the whole subtlety of the hook, extracted so it can be
 * tested without a live EventSource. */
describe("shouldResync", () => {
  const evt = (seq: number, type: string) => ({ seq, type }) as never;

  it("fires when a ControlChanged arrives", () => {
    expect(shouldResync([evt(1, "ControlChanged")], 0)).toEqual({
      resync: true,
      seq: 1,
    });
  });

  it("fires when ControlChanged is not the last event in a batch", () => {
    // The server writes a whole sse.since() batch in one poll and React
    // batches the dispatches into one render, so checking only the tail
    // would miss a ControlChanged followed by a ScanCompleted.
    const batch = [evt(1, "ControlChanged"), evt(2, "ScanCompleted")];
    expect(shouldResync(batch, 0)).toEqual({ resync: true, seq: 2 });
  });

  it("does not re-fire for events already seen", () => {
    const batch = [evt(1, "ControlChanged"), evt(2, "ScanCompleted")];
    expect(shouldResync(batch, 2)).toEqual({ resync: false, seq: 2 });
  });

  it("treats a backwards seq as a fresh session and rescans everything", () => {
    // eventReducer resets the feed when the bus's seq counter moves
    // backwards - a bot restart. That must not wedge the high-water mark.
    expect(shouldResync([evt(1, "ControlChanged")], 500)).toEqual({
      resync: true,
      seq: 1,
    });
  });

  it("is a no-op on an empty feed", () => {
    expect(shouldResync([], 7)).toEqual({ resync: false, seq: 7 });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web/ui && npm test -- useControlSync`
Expected: FAIL — cannot resolve `./useControlSync`

- [ ] **Step 3: Write the hook**

Create `web/ui/lib/useControlSync.ts`:

```typescript
"use client";

import { useEffect, useRef } from "react";
import { useEventStream } from "./useEventStream";
import type { BotEvent } from "./types";

/** Whether a batch of newly-arrived events means we should re-fetch, and the
 * new high-water seq.
 *
 * Checking only the last element is not enough: the server writes a whole
 * sse.since() batch in one poll, EventSource dispatches those messages within
 * one browser task, and React batches the resulting dispatches into a single
 * render - so a ControlChanged followed by a ScanCompleted in the same batch
 * would leave a non-ControlChanged event at the tail and a tail check would
 * never fire.
 *
 * A newest seq LOWER than the high-water mark is a bot restart: eventReducer
 * resets the feed when the bus's seq counter moves backwards. Treat the whole
 * freshly-reset array as new rather than filtering it all away, or the mark
 * stays wedged above every event of the new session. */
export function shouldResync(
  events: readonly BotEvent[],
  lastSeen: number,
): { resync: boolean; seq: number } {
  if (events.length === 0) return { resync: false, seq: lastSeen };
  const latest = events[events.length - 1].seq;
  const sessionReset = latest < lastSeen;
  const fresh = sessionReset ? events : events.filter((e) => e.seq > lastSeen);
  return { resync: fresh.some((e) => e.type === "ControlChanged"), seq: latest };
}

/** Call `onChange` whenever another tab (or another client) changes the
 * controls, so this tab converges without polling. */
export function useControlSync(onChange: () => void): void {
  const { events } = useEventStream();
  const lastSeenSeq = useRef(0);
  useEffect(() => {
    const { resync, seq } = shouldResync(events, lastSeenSeq.current);
    lastSeenSeq.current = seq;
    if (resync) onChange();
    // onChange is expected to be a useCallback; a fresh identity every
    // render would re-run this on every event and defeat the high-water mark.
  }, [events, onChange]);
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web/ui && npm test -- useControlSync`
Expected: PASS

- [ ] **Step 5: Add the types**

In `web/ui/lib/types.ts`, add:

```typescript
export interface BotStatus {
  running: boolean;
  /** Unix seconds when the current bot started, or null when stopped. */
  since: number | null;
  /** The last start failure - a dead emulator, usually. Cleared by a
   * successful start. */
  error: string | null;
}

export interface StrategyList {
  active: string;
  names: string[];
}
```

and add `bot: BotStatus;` to `StatusPayload`.

- [ ] **Step 6: Add the fetchers**

In `web/ui/lib/api.ts`, add. `postJson` is factored out because five of these are the same three lines, and `describeDetail` already exists in this file for exactly this error shape:

```typescript
async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  // 204 and empty bodies are not expected from any of these routes, but a
  // failed parse must still surface as the status, not as a JSON error.
  const parsed = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      (parsed && describeDetail(parsed.detail)) ?? `${method} ${path} -> ${response.status}`,
    );
  }
  return parsed as T;
}

export const fetchStrategies = () => getJson<StrategyList>("/api/strategies");
export const fetchStrategy = (name: string) =>
  getJson<Strategy>(`/api/strategies/${encodeURIComponent(name)}`);
export const saveStrategy = (name: string, body: Strategy) =>
  send<Strategy>(`/api/strategies/${encodeURIComponent(name)}`, "PUT", body);
export const activateStrategy = (name: string) =>
  send<StrategyList>(`/api/strategies/${encodeURIComponent(name)}/activate`, "POST");
export const deleteStrategy = (name: string) =>
  send<StrategyList>(`/api/strategies/${encodeURIComponent(name)}`, "DELETE");

export const startBot = () => send<BotStatus>("/api/bot/start", "POST");
export const stopBot = () => send<BotStatus>("/api/bot/stop", "POST");
export const shutdown = () => send<{ stopping: boolean }>("/api/shutdown", "POST");
```

Delete the old `stopBot` (which posted to `/api/control/stop`) — the name is reused above for the new meaning. Add `BotStatus`, `Strategy` and `StrategyList` to the import list at the top of the file.

- [ ] **Step 7: Commit**

```bash
cd web/ui && npm test && cd ../..
git add web/ui/lib/
git commit -m "feat: fetchers and a shared ControlChanged sync hook

The high-water-seq logic is extracted rather than copied into the second
page: it is the subtlest code on either, and two copies of a comment
block that long is how they start to drift."
```

---

### Task 2: The Strategy page

**Files:**
- Create: `web/ui/components/StrategyEditor.tsx`, `web/ui/components/StrategyEditor.test.tsx`
- Create: `web/ui/components/ProfileBar.tsx`
- Create: `web/ui/app/strategy/page.tsx`, `web/ui/app/strategy/page.test.tsx`
- Modify: `web/ui/components/Sidebar.tsx`

**Interfaces:**
- Consumes: everything from Task 1
- Produces: `StrategyEditor({ value, onChange, disabled })`, `ProfileBar({ list, current, onSelect, onActivate, onDuplicate, onDelete })`

- [ ] **Step 1: Write the failing tests for the editor**

Create `web/ui/components/StrategyEditor.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StrategyEditor } from "./StrategyEditor";
import type { Strategy } from "@/lib/types";

const strategy: Strategy = {
  name: "default",
  actions: [
    { name: "Damage", template: "d.png", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
    { name: "Speed", template: "s.png", enabled: false, threshold: 0.8, brightness_ratio: 0.75 },
  ],
  affordability: "digits",
  interval: 2,
  click_cooldown: 1,
  auto_navigate: false,
  max_runs: null,
  navigation_cooldown: 3,
  screen_confirmations: 2,
};

describe("StrategyEditor", () => {
  it("renders every row in strategy order", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    const rows = screen.getAllByTestId("action-row");
    expect(rows.map((r) => r.getAttribute("data-name"))).toEqual(["Damage", "Speed"]);
  });

  it("shows the row's position, because order is priority", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("moving a row down swaps it with its neighbour", () => {
    const onChange = vi.fn();
    render(<StrategyEditor value={strategy} onChange={onChange} />);
    fireEvent.click(screen.getAllByLabelText("Move down")[0]);
    expect(onChange.mock.calls[0][0].actions.map((a: {name: string}) => a.name)).toEqual([
      "Speed",
      "Damage",
    ]);
  });

  it("cannot move the first row up or the last row down", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    expect(screen.getAllByLabelText("Move up")[0].hasAttribute("disabled")).toBe(true);
    const downs = screen.getAllByLabelText("Move down");
    expect(downs[downs.length - 1].hasAttribute("disabled")).toBe(true);
  });

  it("toggling a row flips only that row's enabled flag", () => {
    const onChange = vi.fn();
    render(<StrategyEditor value={strategy} onChange={onChange} />);
    fireEvent.click(screen.getAllByLabelText("Enabled")[1]);
    const next = onChange.mock.calls[0][0];
    expect(next.actions[1].enabled).toBe(true);
    expect(next.actions[0].enabled).toBe(true);
  });

  it("marks the two fields that only apply on next Start", () => {
    // Without this the page silently lies: you change the value, the server
    // accepts it, and the running bot goes on using the old one.
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    const badges = screen.getAllByText(/applies on next start/i);
    expect(badges.length).toBe(2);
  });

  it("renders an empty max_runs as unlimited", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Max runs").getAttribute("value")).toBe("");
    expect(screen.getByText(/unlimited/i)).toBeTruthy();
  });

  it("greys out an affordability method with no atlas", () => {
    render(
      <StrategyEditor value={strategy} onChange={vi.fn()} available={["brightness"]} />,
    );
    expect(screen.getByLabelText("digits").hasAttribute("disabled")).toBe(true);
  });

  it("disables every input when told to", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} disabled />);
    expect(screen.getByLabelText("Scan interval (s)").hasAttribute("disabled")).toBe(true);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web/ui && npm test -- StrategyEditor`
Expected: FAIL — cannot resolve `./StrategyEditor`

- [ ] **Step 3: Write `StrategyEditor`**

Create `web/ui/components/StrategyEditor.tsx`. It is a controlled component: it never fetches and never writes, it takes a `Strategy` and emits the next one. That keeps every network concern in the page and makes the whole form testable without mocking `fetch`.

```tsx
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
```

- [ ] **Step 4: Run the editor tests**

Run: `cd web/ui && npm test -- StrategyEditor`
Expected: PASS

- [ ] **Step 5: Write `ProfileBar`**

Create `web/ui/components/ProfileBar.tsx`:

```tsx
"use client";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { StrategyList } from "@/lib/types";

export function ProfileBar({
  list, current, dirty, onSelect, onActivate, onDuplicate, onDelete, onSave, onRevert,
}: {
  list: StrategyList;
  current: string;
  dirty: boolean;
  onSelect: (name: string) => void;
  onActivate: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onSave: () => void;
  onRevert: () => void;
}) {
  const isActive = current === list.active;
  return (
    <Card className="flex flex-row flex-wrap items-center gap-2 p-3">
      <select
        aria-label="Strategy"
        value={current}
        onChange={(e) => onSelect(e.target.value)}
        className="rounded-md border bg-background px-2 py-1.5 text-sm"
      >
        {list.names.map((name) => (
          <option key={name} value={name}>
            {name}
            {name === list.active ? " (active)" : ""}
          </option>
        ))}
      </select>

      <Button size="sm" onClick={onSave} disabled={!dirty}>
        Save
      </Button>
      <Button size="sm" variant="outline" onClick={onRevert} disabled={!dirty}>
        Revert
      </Button>
      <Button size="sm" variant="outline" onClick={onActivate} disabled={isActive}>
        {isActive ? "Active" : "Activate"}
      </Button>
      <Button size="sm" variant="outline" onClick={onDuplicate}>
        Duplicate
      </Button>
      <Button
        size="sm" variant="destructive" onClick={onDelete}
        // The server refuses both of these too - this only saves a round
        // trip and makes the rule visible before you click.
        disabled={isActive || list.names.length <= 1}
      >
        Delete
      </Button>

      <span className="ml-auto text-xs text-muted-foreground">
        {isActive
          ? dirty
            ? "unsaved — Save applies this to the running bot"
            : "this is what the bot is running"
          : "not active — edits here do not affect the running bot"}
      </span>
    </Card>
  );
}
```

- [ ] **Step 6: Write the page test**

Create `web/ui/app/strategy/page.test.tsx`. Mock `@/lib/api` wholesale, which is the convention the existing page tests use — read `app/control/page.test.tsx` first and match its mocking style exactly.

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import StrategyPage from "./page";

const strategy = {
  name: "default",
  actions: [
    { name: "Damage", template: "d.png", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
  ],
  affordability: "digits",
  interval: 2,
  click_cooldown: 1,
  auto_navigate: false,
  max_runs: null,
  navigation_cooldown: 3,
  screen_confirmations: 2,
};

const api = vi.hoisted(() => ({
  fetchStrategies: vi.fn(),
  fetchStrategy: vi.fn(),
  saveStrategy: vi.fn(),
  activateStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
  fetchControl: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/lib/useControlSync", () => ({ useControlSync: () => {} }));

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchStrategies.mockResolvedValue({ active: "default", names: ["default", "crit"] });
  api.fetchStrategy.mockResolvedValue(strategy);
  api.fetchControl.mockResolvedValue({
    paused: false, strategy, affordability_available: ["digits", "brightness"],
  });
  api.saveStrategy.mockImplementation((_n: string, body: unknown) => Promise.resolve(body));
});

describe("StrategyPage", () => {
  it("loads the active profile on arrival", async () => {
    render(<StrategyPage />);
    await waitFor(() => expect(screen.getByTestId("action-row")).toBeTruthy());
    expect(api.fetchStrategy).toHaveBeenCalledWith("default");
  });

  it("saving sends the whole profile under the selected name", async () => {
    render(<StrategyPage />);
    await waitFor(() => screen.getByLabelText("Scan interval (s)"));
    fireEvent.blur(screen.getByLabelText("Scan interval (s)"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() =>
      expect(api.saveStrategy).toHaveBeenCalledWith(
        "default",
        expect.objectContaining({ interval: 5 }),
      ),
    );
  });

  it("Save is disabled until something changes", async () => {
    render(<StrategyPage />);
    await waitFor(() => screen.getByText("Save"));
    expect(screen.getByText("Save").hasAttribute("disabled")).toBe(true);
  });

  it("Revert discards local edits without calling the server", async () => {
    render(<StrategyPage />);
    await waitFor(() => screen.getByLabelText("Scan interval (s)"));
    fireEvent.blur(screen.getByLabelText("Scan interval (s)"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByText("Revert"));
    await waitFor(() =>
      expect(screen.getByLabelText("Scan interval (s)").getAttribute("value")).toBe("2"),
    );
    expect(api.saveStrategy).not.toHaveBeenCalled();
  });

  it("shows the server's reason when a save is rejected", async () => {
    api.saveStrategy.mockRejectedValue(new Error("interval: must be between 0.1 and 3600"));
    render(<StrategyPage />);
    await waitFor(() => screen.getByLabelText("Scan interval (s)"));
    fireEvent.blur(screen.getByLabelText("Scan interval (s)"), {
      target: { value: "9999" },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(screen.getByText(/must be between/)).toBeTruthy());
  });

  it("switching profiles fetches the other one", async () => {
    render(<StrategyPage />);
    await waitFor(() => screen.getByLabelText("Strategy"));
    fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "crit" } });
    await waitFor(() => expect(api.fetchStrategy).toHaveBeenCalledWith("crit"));
  });
});
```

- [ ] **Step 7: Write the page**

Create `web/ui/app/strategy/page.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { ProfileBar } from "@/components/ProfileBar";
import { StrategyEditor } from "@/components/StrategyEditor";
import {
  activateStrategy, deleteStrategy, fetchControl, fetchStrategies,
  fetchStrategy, saveStrategy,
} from "@/lib/api";
import { useControlSync } from "@/lib/useControlSync";
import type { Strategy, StrategyList } from "@/lib/types";

export default function StrategyPage() {
  const [list, setList] = useState<StrategyList | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  // Two copies on purpose: `saved` is what the server last confirmed and
  // `draft` is what is on screen. Their difference is what makes Save and
  // Revert meaningful, and what stops a half-typed form being sent.
  const [saved, setSaved] = useState<Strategy | null>(null);
  const [draft, setDraft] = useState<Strategy | null>(null);
  const [available, setAvailable] = useState<string[] | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (name?: string) => {
    const listing = await fetchStrategies();
    setList(listing);
    const target = name ?? listing.active;
    setSelected(target);
    const loaded = await fetchStrategy(target);
    setSaved(loaded);
    setDraft(loaded);
  }, []);

  useEffect(() => {
    load().catch((e) => setError(String(e)));
    // Only for which affordability methods this machine can actually serve -
    // the atlas either built or it did not, and the strategy has no way to
    // know.
    fetchControl()
      .then((c) => setAvailable(c.affordability_available))
      .catch(() => setAvailable(undefined));
  }, [load]);

  // Another tab activated a profile, or the CLI overrode one at startup.
  // Re-read the listing, but keep the selection - yanking the reader to a
  // different profile mid-edit would be worse than being briefly stale.
  const onRemoteChange = useCallback(() => {
    fetchStrategies().then(setList).catch(() => {});
  }, []);
  useControlSync(onRemoteChange);

  if (!list || !draft || !saved || !selected) {
    return <p className="text-sm text-muted-foreground">{error ?? "Loading…"}</p>;
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);

  async function guard(work: () => Promise<void>) {
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      {error ? (
        <p className="rounded-md border border-red-500 p-2 text-sm text-red-500">{error}</p>
      ) : null}

      <ProfileBar
        list={list}
        current={selected}
        dirty={dirty}
        onSelect={(name) => void guard(() => load(name))}
        onSave={() =>
          void guard(async () => {
            // The server is the validator and the server's answer is what we
            // render - never the local draft, which may differ from what was
            // accepted.
            const confirmed = await saveStrategy(selected, draft);
            setSaved(confirmed);
            setDraft(confirmed);
          })
        }
        onRevert={() => setDraft(saved)}
        onActivate={() =>
          void guard(async () => {
            setList(await activateStrategy(selected));
          })
        }
        onDuplicate={() =>
          void guard(async () => {
            const name = window.prompt("Name for the copy", `${selected}-copy`);
            if (!name) return;
            const copy = { ...draft, name };
            await saveStrategy(name, copy);
            await load(name);
          })
        }
        onDelete={() =>
          void guard(async () => {
            if (!window.confirm(`Delete strategy "${selected}"?`)) return;
            const after = await deleteStrategy(selected);
            setList(after);
            await load(after.active);
          })
        }
      />

      <StrategyEditor value={draft} onChange={setDraft} available={available} />
    </div>
  );
}
```

- [ ] **Step 8: Add the sidebar link**

In `web/ui/components/Sidebar.tsx`, add to `LINKS`, between Errors and Control:

```typescript
  { href: "/strategy/", label: "Strategy" },
```

The trailing slash is required — `next.config.ts` sets `trailingSlash: true`, and `StaticFiles(html=True)` resolves `/strategy/` to `strategy/index.html`. Without it the link 404s in the built dashboard while working fine in `npm run dev`, which is the worst way to find out.

- [ ] **Step 9: Run the frontend tests**

Run: `cd web/ui && npm test`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add web/ui/components/StrategyEditor.tsx web/ui/components/StrategyEditor.test.tsx web/ui/components/ProfileBar.tsx web/ui/app/strategy/ web/ui/components/Sidebar.tsx
git commit -m "feat: the Strategy page

A controlled editor that never fetches, plus a page that owns every
network concern - which is what makes the whole form testable without
mocking fetch.

Draft and saved are kept separately so Save and Revert mean something,
and the two start-only fields carry a badge: without it the page would
silently lie about a control that does nothing until the next Start."
```

---

### Task 3: The Control page, the pill, and the docs

**Files:**
- Rewrite: `web/ui/app/control/page.tsx`, `web/ui/app/control/page.test.tsx`
- Modify: `web/ui/components/StatBar.tsx`
- Modify: `README.md`

**Interfaces:**
- Consumes: `startBot`, `stopBot`, `shutdown`, `fetchControl`, `patchControl`, `useControlSync`, `StatusPayload.bot`

- [ ] **Step 1: Write the failing tests**

Replace `web/ui/app/control/page.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ControlPage from "./page";

const strategy = {
  name: "default",
  actions: [
    { name: "Damage", template: "d.png", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
    { name: "Speed", template: "s.png", enabled: false, threshold: 0.9, brightness_ratio: 0.75 },
  ],
  affordability: "digits",
  interval: 2,
  click_cooldown: 1,
  auto_navigate: true,
  max_runs: null,
  navigation_cooldown: 3,
  screen_confirmations: 2,
};

const api = vi.hoisted(() => ({
  fetchControl: vi.fn(),
  patchControl: vi.fn(),
  fetchStatus: vi.fn(),
  startBot: vi.fn(),
  stopBot: vi.fn(),
  shutdown: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/lib/useControlSync", () => ({ useControlSync: () => {} }));

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchControl.mockResolvedValue({
    paused: false, strategy, affordability_available: ["digits", "brightness"],
  });
  api.fetchStatus.mockResolvedValue({ bot: { running: false, since: null, error: null } });
  api.startBot.mockResolvedValue({ running: true, since: 1, error: null });
  api.stopBot.mockResolvedValue({ running: false, since: null, error: null });
  api.patchControl.mockImplementation((p: Record<string, unknown>) =>
    Promise.resolve({ paused: !!p.paused, strategy, affordability_available: [] }),
  );
});

describe("ControlPage", () => {
  it("offers Start when the bot is stopped", async () => {
    render(<ControlPage />);
    await waitFor(() => expect(screen.getByText("Start")).toBeTruthy());
    expect(screen.queryByText("Stop bot")).toBeNull();
  });

  it("offers Stop when the bot is running", async () => {
    api.fetchStatus.mockResolvedValue({ bot: { running: true, since: 1, error: null } });
    render(<ControlPage />);
    await waitFor(() => expect(screen.getByText("Stop bot")).toBeTruthy());
    expect(screen.queryByText("Start")).toBeNull();
  });

  it("Start calls the bot route, not the shutdown route", async () => {
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Start"));
    fireEvent.click(screen.getByText("Start"));
    await waitFor(() => expect(api.startBot).toHaveBeenCalled());
    expect(api.shutdown).not.toHaveBeenCalled();
  });

  it("shows a dead emulator's message instead of failing silently", async () => {
    api.startBot.mockRejectedValue(new Error("no emulator at 127.0.0.1:5555"));
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Start"));
    fireEvent.click(screen.getByText("Start"));
    await waitFor(() => expect(screen.getByText(/no emulator/)).toBeTruthy());
  });

  it("pause is a control patch, not a lifecycle call", async () => {
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Pause"));
    fireEvent.click(screen.getByText("Pause"));
    await waitFor(() => expect(api.patchControl).toHaveBeenCalledWith({ paused: true }));
    expect(api.stopBot).not.toHaveBeenCalled();
  });

  it("summarises the active strategy read-only and links to edit it", async () => {
    render(<ControlPage />);
    await waitFor(() => expect(screen.getByText("default")).toBeTruthy());
    // The rows are shown, but not as inputs - editing lives on /strategy/.
    expect(screen.queryByLabelText("Damage threshold")).toBeNull();
    expect(screen.getByRole("link", { name: /edit strategy/i }).getAttribute("href"))
      .toBe("/strategy/");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web/ui && npm test -- control`
Expected: FAIL — the page still renders the old form

- [ ] **Step 3: Rewrite the Control page**

Replace `web/ui/app/control/page.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  fetchControl, fetchStatus, patchControl, shutdown, startBot, stopBot,
} from "@/lib/api";
import { useControlSync } from "@/lib/useControlSync";
import type { BotStatus, ControlPayload } from "@/lib/types";

/** Session concerns only.
 *
 * Everything this page used to edit is policy, and policy lives on
 * /strategy/ now - the split follows the model: Controls holds `paused` and
 * a Strategy, and only `paused` is a fact about this bot right now rather
 * than a decision you would save under a name. */
export default function ControlPage() {
  const [control, setControl] = useState<ControlPayload | null>(null);
  const [bot, setBot] = useState<BotStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setControl(await fetchControl());
  }, []);

  useEffect(() => {
    reload().catch((e) => setError(String(e)));
  }, [reload]);

  // Polled rather than pushed: starting and stopping are not events on the
  // bus, and a two-second lag on a button you just pressed is invisible
  // because the response updates it immediately anyway.
  useEffect(() => {
    let alive = true;
    const tick = () =>
      void fetchStatus()
        .then((s) => alive && setBot(s.bot))
        .catch(() => {});
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useControlSync(useCallback(() => void reload().catch(() => {}), [reload]));

  async function guard(work: () => Promise<void>) {
    setError(null);
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!control) {
    return <p className="text-sm text-muted-foreground">{error ?? "Loading…"}</p>;
  }

  const running = bot?.running ?? false;
  const s = control.strategy;

  return (
    <div className="flex max-w-xl flex-col gap-4">
      {error ? (
        <p className="rounded-md border border-red-500 p-2 text-sm text-red-500">{error}</p>
      ) : null}

      <Card className="flex-row flex-wrap items-center gap-2 p-3">
        {running ? (
          <Button
            variant="outline" disabled={busy}
            onClick={() => void guard(async () => setBot(await stopBot()))}
          >
            Stop bot
          </Button>
        ) : (
          <Button
            disabled={busy}
            onClick={() => void guard(async () => setBot(await startBot()))}
          >
            Start
          </Button>
        )}

        <Button
          variant="outline" disabled={busy || !running}
          onClick={() => void guard(async () => setControl(await patchControl({
            paused: !control.paused,
          })))}
        >
          {control.paused ? "Resume" : "Pause"}
        </Button>

        <Button
          variant="destructive" disabled={busy}
          onClick={() =>
            void guard(async () => {
              // Distinct from Stop bot, and worth confirming: this ends the
              // dashboard too, and there is no button to bring it back.
              if (!window.confirm("Shut down the bot AND the dashboard?")) return;
              await shutdown();
            })
          }
        >
          Shut down
        </Button>

        <span className="self-center text-sm text-muted-foreground">
          {!running
            ? "stopped"
            : control.paused
              ? "paused — scanning, not tapping"
              : "running"}
        </span>
      </Card>

      {bot?.error ? (
        <p className="rounded-md border border-amber-500 p-2 text-sm text-amber-600">
          Last start failed: {bot.error}
        </p>
      ) : null}

      <Card className="gap-2 p-3 text-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-xs uppercase tracking-wide text-muted-foreground">
            Active strategy
          </h2>
          <Link href="/strategy/" className="text-xs underline">
            Edit strategy
          </Link>
        </div>
        <p className="font-medium">{s.name}</p>
        <p className="text-muted-foreground">
          {s.interval}s scans · {s.affordability} · auto-navigate{" "}
          {s.auto_navigate ? "on" : "off"} ·{" "}
          {s.max_runs === null ? "unlimited runs" : `${s.max_runs} runs`}
        </p>
        <ol className="list-inside list-decimal text-muted-foreground">
          {s.actions.map((row) => (
            <li key={row.name} className={row.enabled ? "" : "line-through opacity-60"}>
              {row.name}
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Add the pill to `StatBar`**

In `web/ui/components/StatBar.tsx`, add a pill driven by `status?.bot`. Read the file first and match its existing chip markup; the content is:

```tsx
      <span
        className={`rounded-full px-2 py-0.5 text-xs ${
          status?.bot?.running
            ? "bg-emerald-500/15 text-emerald-600"
            : "bg-muted text-muted-foreground"
        }`}
      >
        {status?.bot?.running ? "bot running" : "bot stopped"}
      </span>
```

`status?.bot?.running` uses optional chaining on `bot` deliberately: a browser holding a page from before this deploy, talking to a server that has been restarted, would otherwise crash on a missing key.

- [ ] **Step 5: Run the frontend tests**

Run: `cd web/ui && npm test`
Expected: PASS

- [ ] **Step 6: Rebuild and verify the Python build test**

```bash
cd web/ui && npm run build && cd ../..
uv run pytest tests/test_web_build.py -q -p no:allure_pytest
```

Expected: PASS. `test_web_build.py` does not enumerate pages, and `tools/ui_manifest.py` hashes the `app/`, `components/` and `lib/` roots wholesale — so a new page needs no manifest edit, but it *does* change the hash, which is why the rebuild above is not optional.

- [ ] **Step 7: Update the README**

- The dashboard section's "five pages behind a sidebar" becomes **six**, with a new bullet:

```markdown
- **Strategy** — the whole decision policy: which upgrades to buy and in
  what order, per-row match and brightness thresholds, loop timing, and run
  policy. Named profiles live in `strategies/*.json`, switchable live and
  editable by hand. Two fields — navigation cooldown and screen
  confirmations — configure objects built once per bot, so they are labelled
  *applies on next Start*.
```

- Rewrite the **control** paragraph: it now covers Start, Stop bot, Shut down and pause, and points at the Strategy page for everything else.
- Note `--idle` beside `--web` in the prose, not only in the flags table: "`--web --idle` serves the dashboard without starting a bot — press **Start** in the browser."
- The security warning gains the new powers, matching the spec's section 13.

- [ ] **Step 8: End-to-end check by hand**

```bash
uv run tower_bot.py --web --idle
```

Then in a browser at `http://127.0.0.1:8765`:

1. Status bar shows **bot stopped**; Control shows **Start**.
2. Strategy page lists `default`, shows four rows in `config.ACTIONS` order.
3. Move a row down, Save — the page re-renders in the new order.
4. `cat strategies/default.json` — the new order is on disk.
5. Duplicate to `crit`, change its interval, Save, Activate — Control's summary shows `crit`.
6. Press Start (with the emulator running) — the pill turns green, the Live page's feed moves.
7. Change the interval on the Strategy page and Save while running — the scan rate changes without a restart.
8. Stop bot — the pill greys, the dashboard stays up, Start is offered again.
9. Start again — run ids in the Runs table continue rather than restarting at 1.
10. Shut down — the process exits.

Step 9 is the one worth doing carefully; it is the failure that only appears on a second start.

- [ ] **Step 9: Commit**

```bash
git add web/ui/app/control/ web/ui/components/StatBar.tsx README.md web/static/
git commit -m "feat: Control keeps session concerns, Strategy owns policy

Start / Stop bot / Shut down are three buttons because they are three
different things - the old single Stop killed the dashboard you pressed
it from.

The strategy summary here is read-only and links to /strategy/: two
editors for one object is how they disagree."
```

---

## Self-Review Notes

**Spec coverage.** Section 9 in full: the sidebar's sixth entry, the Control/Strategy split, the profile selector with all four operations, the three editor sections, the *applies on next Start* badge, up/down instead of drag-and-drop, the live-vs-file rule, and the `StatBar` pill.

**Where the `max_runs` limitation lands.** Plan 2 documented that `PATCH` cannot carry a null. This plan never hits it: the Strategy page always `PUT`s the whole profile, and the Control page only ever patches `paused`. That is why the page saves rather than patches, and the comment on the `max_runs` input says so.

**Placeholder scan.** Three steps deliberately say "read the existing file first and match it" — `StatBar`'s chip markup (Task 3 step 4), the page-test mocking style (Task 2 step 6), and `test_web_build.py`'s assertions (Task 3 step 6). Each names exactly what to match and gives the content to add, so none is a "figure it out" step; they exist because the surrounding markup is the thing being matched and copying it into the plan would be a second copy to drift.

**Type consistency.** `Strategy`, `ActionRule`, `BotStatus` and `StrategyList` are used here exactly as plan 2 (`types.ts`) and plan 3 (the `bot` block, the runner's return shape) define them. `stopBot` is redefined in Task 1 to mean `/api/bot/stop`; the old export pointing at `/api/control/stop` is deleted in the same step, so no call site can reach the removed route.
