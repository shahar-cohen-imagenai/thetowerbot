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
          variant="outline" disabled={busy}
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
