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
