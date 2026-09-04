"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { StrategyList } from "@/lib/types";

export function ProfileBar({
  list, current, dirty, busy = false,
  onSelect, onActivate, onDuplicate, onDelete, onSave, onRevert,
}: {
  list: StrategyList;
  current: string;
  dirty: boolean;
  /** A request this bar started is still in flight. Every control here
   * either writes or replaces what is on screen, so a second click during
   * one is never what the user meant - a double-clicked Save is two PUTs. */
  busy?: boolean;
  onSelect: (name: string) => void;
  onActivate: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onSave: () => void;
  onRevert: () => void;
}) {
  const isActive = current === list.active;
  // Editing the running bot's own profile with unsaved changes is the one
  // state on this page that can surprise someone. It gets the whole bar.
  const armed = isActive && dirty;

  return (
    <div
      className={cn(
        // Sticky: this bar owns Save, and it used to scroll out of sight
        // within the first screen - so while editing shopping rules at the
        // bottom you could see neither which profile you were editing, nor
        // whether it was the live one, nor whether you had unsaved work.
        "sticky top-0 z-20 flex flex-col gap-2 rounded-xl border bg-card p-3",
        armed && "border-warn bg-warn-surface",
      )}
    >
      <div className="flex flex-row flex-wrap items-center gap-2">
        <select
          aria-label="Strategy"
          value={current}
          disabled={busy}
          onChange={(e) => onSelect(e.target.value)}
          className="h-8 rounded-lg border border-input bg-background px-2 text-sm"
        >
          {list.names.map((name) => (
            <option key={name} value={name}>
              {name}
              {name === list.active ? " (active)" : ""}
            </option>
          ))}
        </select>

        {/* A span, not a button: this bar's contract is exactly six
            focusable controls, all of which disable together. */}
        <span
          className={cn(
            "rounded-full px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.1em]",
            isActive ? "bg-live-surface text-live" : "bg-muted text-muted-foreground",
          )}
        >
          {isActive ? "active" : "draft"}
          {dirty ? " · unsaved" : ""}
        </span>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={onSave} disabled={busy || !dirty}>
            Save
          </Button>
          <Button size="sm" variant="outline" onClick={onRevert} disabled={busy || !dirty}>
            Revert
          </Button>
          <Button size="sm" variant="outline" onClick={onActivate} disabled={busy || isActive}>
            {isActive ? "Active" : "Activate"}
          </Button>
          <Button size="sm" variant="outline" onClick={onDuplicate} disabled={busy}>
            Duplicate
          </Button>
          <Button
            size="sm" variant="danger" onClick={onDelete}
            // The server refuses both of these too - this only saves a round
            // trip and makes the rule visible before you click.
            disabled={busy || isActive || list.names.length <= 1}
          >
            Delete
          </Button>
        </div>
      </div>

      {/* The copy is unchanged - it was already the best-written text in the
          repo. All that was wrong with it was that the sentence explaining
          that Save writes into a running bot was set at 12px in the lowest
          contrast colour available, pushed to the far right of a wrapping
          row, where at narrow widths it dropped *below* the button it warned
          about. */}
      <p
        className={cn(
          "text-sm",
          armed ? "font-medium text-warn" : "text-xs text-muted-foreground",
        )}
      >
        {isActive
          ? dirty
            ? "unsaved — Save applies this to the running bot"
            : "this is what the bot is running"
          : "not active — edits here do not affect the running bot"}
      </p>
    </div>
  );
}
