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
