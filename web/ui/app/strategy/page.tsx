"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ProfileBar } from "@/components/ProfileBar";
import { ShoppingEditor } from "@/components/ShoppingEditor";
import { StrategyEditor } from "@/components/StrategyEditor";
import { StrategyNav } from "@/components/StrategyNav";
import { AutopilotEditor } from "@/components/AutopilotEditor";
import { AdvisorPanel } from "@/components/AdvisorPanel";
import { useAutopilot } from "@/lib/useAutopilot";
import {
  activateStrategy, deleteStrategy, fetchControl, fetchStrategies,
  fetchStrategy, saveStrategy,
  fetchUpgrades, fetchAutopilotPresets,
  postAutopilotCommand,
} from "@/lib/api";
import { useControlSync } from "@/lib/useControlSync";
import { errorText } from "@/lib/utils";
import type { AutopilotCommand, AutopilotPreset, Upgrade, Strategy, StrategyList } from "@/lib/types";

// Mirrors strategy.py's NAME_PATTERN, because a profile name becomes a
// filename. Checking it here turns "my copy" from a 422 round trip into a
// re-prompt that says the rule.
const NAME_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const NAME_RULE =
  "A strategy name must be 1-64 characters of letters, digits, '-' or '_'.";

export default function StrategyPage() {
  const { snapshot } = useAutopilot();
  const [catalog, setCatalog] = useState<Upgrade[]>([]);
  const [presets, setPresets] = useState<AutopilotPreset[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [commandPending, setCommandPending] = useState(false);
  const [commandResult, setCommandResult] = useState<string | null>(null);
  async function command(command: AutopilotCommand) {
    setCommandPending(true);
    setCommandResult(null);
    try {
      await postAutopilotCommand(command);
      setCommandResult("Command queued. Watch Live for the verified outcome.");
    } catch (error) {
      setCommandResult(errorText(error));
    } finally {
      setCommandPending(false);
    }
  }
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const [upgrades, choices] = await Promise.all([fetchUpgrades(), fetchAutopilotPresets()]);
        if (alive) { setCatalog(upgrades); setPresets(choices); }
      } catch {
        if (alive) setCatalogError("Upgrade catalog unavailable. Legacy controls remain available.");
      }
    })();
    return () => { alive = false; };
  }, []);
  const [list, setList] = useState<StrategyList | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  // Two copies on purpose: `saved` is what the server last confirmed and
  // `draft` is what is on screen. Their difference is what makes Save and
  // Revert meaningful, and what stops a half-typed form being sent.
  const [saved, setSaved] = useState<Strategy | null>(null);
  const [draft, setDraft] = useState<Strategy | null>(null);
  const [available, setAvailable] = useState<string[] | undefined>(undefined);
  const [speedValues, setSpeedValues] = useState<number[] | undefined>(undefined);
  const [shoppingDisabledReason, setShoppingDisabledReason] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const dirty =
    draft !== null && saved !== null && JSON.stringify(draft) !== JSON.stringify(saved);

  // Read by onRemoteChange, which useControlSync requires to keep one stable
  // identity - it cannot close over `selected`/`dirty` directly.
  const selectedRef = useRef<string | null>(null);
  const dirtyRef = useRef(false);
  useEffect(() => {
    selectedRef.current = selected;
    dirtyRef.current = dirty;
  }, [selected, dirty]);

  const load = useCallback(async (name?: string) => {
    const listing = await fetchStrategies();
    const target = name ?? listing.active;
    const loaded = await fetchStrategy(target);
    // Nothing is committed until every fetch has resolved. Committing
    // `selected` first meant a rejected fetchStrategy (a 404 from a profile
    // another tab deleted, a 422 from one hand-edited into invalid JSON)
    // left the selector naming one profile while the editor still held
    // another's body - and the next Save would write that body under this
    // name. It also makes two fast selector clicks land in call order
    // rather than in whichever order the network happened to answer.
    setList(listing);
    setSelected(target);
    setSaved(loaded);
    setDraft(loaded);
  }, []);

  useEffect(() => {
    load().catch((e) => setError(errorText(e)));
    // Only for which affordability methods this machine can actually serve -
    // the atlas either built or it did not, and the strategy has no way to
    // know. Same call carries shopping_disabled_reason for the same reason:
    // whether shopping can ever approve a purchase is a machine capability
    // fact, not something the strategy document itself can express.
    fetchControl()
      .then((c) => {
        setAvailable(c.affordability_available);
        setShoppingDisabledReason(c.shopping_disabled_reason);
        setSpeedValues(c.speed_values);
      })
      .catch(() => setAvailable(undefined));
  }, [load]);

  // Another tab (or the CLI) changed the controls. This is the only page
  // that PUTs whole documents, so a stale `saved` here does not go stale
  // quietly - it reverts the other tab's work on the next Save. Converge:
  // the listing always, and this profile's body too. `saved` unconditionally,
  // so `dirty`, Revert and the "unsaved" hint all measure against what the
  // server actually holds; `draft` only while this tab is clean, so a reader
  // catches up without an editor's work being yanked out from under them.
  const onRemoteChange = useCallback(() => {
    void fetchStrategies().then(setList).catch(() => {});
    if (!selectedRef.current) return;
    void fetchStrategy(selectedRef.current)
      .then((fresh) => {
        setSaved(fresh);
        if (!dirtyRef.current) setDraft(fresh);
      })
      .catch(() => {});
  }, []);
  useControlSync(onRemoteChange);

  if (!list || !draft || !saved || !selected) {
    return <p className="text-sm text-muted-foreground">{error ?? "Loading…"}</p>;
  }

  async function guard(work: () => Promise<void>) {
    setError(null);
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex gap-6">
      <StrategyNav draft={draft} saved={saved} />

      <div className="flex min-w-0 max-w-3xl flex-1 flex-col gap-4">
      {error ? (
        <p className="rounded-md border border-danger p-2 text-sm text-danger">{error}</p>
      ) : null}

      <ProfileBar
        list={list}
        current={selected}
        dirty={dirty}
        busy={busy}
        onSelect={(name) =>
          void guard(async () => {
            // Duplicate carries a dirty draft into a new profile; switching
            // throws it away. The bar is displaying the word "unsaved" at
            // this very moment, so ask rather than silently discarding.
            if (dirty && !window.confirm(`Discard unsaved changes to "${selected}"?`)) return;
            await load(name);
          })
        }
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
            let name = window.prompt("Name for the copy", `${selected}-copy`);
            // Re-prompt rather than round-trip: the server would answer a
            // bare 422, which arrives as a red banner over a dialog the user
            // has already dismissed. Cancel (null) still leaves the loop.
            while (name !== null && !NAME_PATTERN.test(name)) {
              name = window.prompt(NAME_RULE, name);
            }
            if (!name) return;
            const copy = { ...draft, name };
            // Discard saveStrategy's response and re-load rather than just
            // setSaved/setDraft(copy) here: saveStrategy returns only the
            // Strategy, but the new name also has to appear in the profile
            // *list*, which only load() re-fetches. Not a redundant round
            // trip - it's the only call that refreshes both pieces of state.
            await saveStrategy(name, copy);
            await load(name);
          })
        }
        onDelete={() =>
          void guard(async () => {
            if (!window.confirm(`Delete strategy "${selected}"?`)) return;
            const after = await deleteStrategy(selected);
            // No setList(after) here - load() re-fetches the listing itself,
            // and setting it first only paints a state one fetch older.
            await load(after.active);
          })
        }
      />

      {commandResult ? <p role="status" className="text-sm">{commandResult}</p> : null}
      <AutopilotEditor value={draft} onChange={setDraft} catalog={catalog} presets={presets} snapshot={snapshot} disabled={busy} error={catalogError} onCommand={(next) => void command(next)} commandPending={commandPending} />

      <StrategyEditor
        value={draft} onChange={setDraft} available={available}
        speedValues={speedValues} disabled={busy} hidePurchases={draft.autopilot?.enabled} legacyPurchases
      />

      <ShoppingEditor
        shopping={draft.shopping}
        onChange={(shopping) => setDraft({ ...draft, shopping })}
        disabled={busy}
        disabledReason={shoppingDisabledReason}
        hideWorkshopRows={catalog.length > 0}
      />
      <AdvisorPanel profile={selected} value={draft} onChange={setDraft} disabled={busy} />
      </div>
    </div>
  );
}
