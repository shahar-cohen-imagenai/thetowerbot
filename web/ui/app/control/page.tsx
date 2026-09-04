"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SectionCard } from "@/components/ui/section-card";
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
        <p className="rounded-md border border-danger p-2 text-sm text-danger">{error}</p>
      ) : null}

      {/* The cockpit: what the bot is doing, at a size you can read from
          across the room, with the two session controls under it. */}
      <Card size="sm" className="gap-3">
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-[0.13em] text-faint-foreground">
                Bot
              </div>
              {/* Never says "stale" - the status line below owns that word,
                  and two elements saying it would be one too many. */}
              <div
                className={`mt-1 font-mono text-3xl font-medium leading-none ${
                  !running ? "text-muted-foreground" : control.paused ? "text-warn" : "text-live"
                }`}
              >
                {!running ? "STOPPED" : control.paused ? "PAUSED" : "RUNNING"}
              </div>
            </div>
            <StatusBadge state={stale ? "warn" : !running ? "idle" : control.paused ? "warn" : "live"}>
              {stale ? "unreachable" : !running ? "idle" : control.paused ? "holding" : "live"}
            </StatusBadge>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {running ? (
              <Button
                size="lg" variant="outline" disabled={busy}
                onClick={() => void guard(async () => setBot(await stopBot()))}
              >
                Stop bot
              </Button>
            ) : (
              <Button size="lg" disabled={busy} onClick={() => void guard(start)}>
                Start
              </Button>
            )}

            <Button
              size="lg" variant="outline" disabled={busy || !running}
              onClick={() => void guard(async () => setControl(await patchControl({
                paused: !control.paused,
              })))}
            >
              {control.paused ? "Resume" : "Pause"}
            </Button>
          </div>

          <span
            className={`text-sm ${stale ? "text-warn" : "text-muted-foreground"}`}
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
            <span className="text-sm text-muted-foreground">{notice}</span>
          ) : null}
        </CardContent>
      </Card>

      {bot?.error ? (
        <p className="rounded-md border border-warn p-2 text-sm text-warn">
          Last start failed: {bot.error}
        </p>
      ) : null}

      <SectionCard
        title="Active strategy"
        action={
          <Link href="/strategy/" className="text-xs underline">
            Edit strategy
          </Link>
        }
      >
        <p className="font-mono text-sm font-medium">{s.name}</p>
        <p className="mt-1 text-sm text-muted-foreground">
          {s.interval}s scans · {s.affordability} · auto-navigate{" "}
          {s.auto_navigate ? "on" : "off"} ·{" "}
          {s.max_runs === null ? "unlimited runs" : `${s.max_runs} runs`}
        </p>
        {/* Ordered chips rather than a struck-through list: the order is the
            priority, and a disabled rule should read as absent from the queue
            rather than as a line of text with a pen through it. */}
        <ol className="mt-2 flex flex-wrap gap-1.5">
          {s.actions.map((row, i) => (
            <li
              key={row.name}
              className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
                row.enabled
                  ? "bg-muted text-foreground"
                  : "text-faint-foreground line-through"
              }`}
            >
              {i + 1}. {row.name}
            </li>
          ))}
        </ol>
      </SectionCard>

      {/* Shut down used to sit inline with Start and Pause at identical
          weight. It ends the dashboard too, and nothing on screen can bring
          it back - so it gets its own footer and the solid danger variant.
          Deliberately not the class `border-danger`: the error banner above
          is found by that exact selector. */}
      <div className="mt-2 rounded-xl border border-t-2 border-t-danger bg-card p-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-[0.13em] text-danger">
              Danger zone
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Ends the bot and this dashboard together. There is no button here to bring it back.
            </p>
          </div>
          <Button
            variant="danger" disabled={busy}
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
        </div>
      </div>
    </div>
  );
}
