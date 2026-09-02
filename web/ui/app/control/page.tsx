"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  ApiError, fetchControl, fetchStatus, patchControl, shutdown, startBot, stopBot,
} from "@/lib/api";
import { useControlSync } from "@/lib/useControlSync";
import { errorText } from "@/lib/utils";
import type { BotStatus, ControlPayload } from "@/lib/types";

/** Consecutive status-poll failures before the page admits it is stale.
 *
 * One failure is a blip and flapping the status text on it would be worse
 * than silence; three (six seconds) is a dead server. Saying nothing at all
 * was the real bug: a null `bot` renders as "stopped" and offers Start for a
 * bot that may well be running. */
const STALE_AFTER_FAILURES = 3;

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
  /** Something happened that is not a failure - said in the status text's own
   * muted voice, not the red banner. */
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pollFailures, setPollFailures] = useState(0);

  const reload = useCallback(async () => {
    setControl(await fetchControl());
  }, []);

  useEffect(() => {
    reload().catch((e) => setError(errorText(e)));
  }, [reload]);

  // Polled rather than pushed: starting and stopping are not events on the
  // bus, and a two-second lag on a button you just pressed is invisible
  // because the response updates it immediately anyway.
  useEffect(() => {
    let alive = true;
    const tick = () =>
      void fetchStatus()
        .then((s) => {
          if (!alive) return;
          setBot(s.bot);
          setPollFailures(0);
        })
        .catch(() => {
          if (alive) setPollFailures((n) => n + 1);
        });
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
    setNotice(null);
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  /** Start, with 409 read as an answer rather than a fault.
   *
   * A 409 means another tab already started the bot - the bot the user asked
   * for is running. Banner it in red and the next poll flips the button to
   * "Stop bot" two seconds later while the failure notice sits above it,
   * unread by anything until the next guard() clears it. */
  async function start(): Promise<void> {
    try {
      setBot(await startBot());
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 409) throw e;
      setBot((await fetchStatus()).bot);
      setNotice("already running — started from somewhere else");
    }
  }

  if (!control) {
    return <p className="text-sm text-muted-foreground">{error ?? "Loading…"}</p>;
  }

  const running = bot?.running ?? false;
  const stale = pollFailures >= STALE_AFTER_FAILURES;
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
          <Button disabled={busy} onClick={() => void guard(start)}>
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

        <span
          className={`self-center text-sm ${stale ? "text-amber-600" : "text-muted-foreground"}`}
        >
          {stale
            ? "stale — /api/status is not answering"
            : !running
              ? "stopped"
              : control.paused
                ? "paused — scanning, not tapping"
                : "running"}
        </span>

        {notice ? (
          <span className="self-center text-sm text-muted-foreground">{notice}</span>
        ) : null}
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
