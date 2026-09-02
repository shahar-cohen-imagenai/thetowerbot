"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { fetchControl, patchControl, shutdown } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";
import type { ControlPayload, Strategy } from "@/lib/types";

export default function ControlPage() {
  const [control, setControl] = useState<ControlPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped on a rejected patch, and nowhere else. The interval Input below
  // is keyed on this alongside control.interval: a 422 leaves control.interval
  // unchanged (send() deliberately does not call setControl on failure), so
  // the key alone would never remount the field and the browser's dirty,
  // rejected value would sit there looking live. Bumping this forces that
  // remount without touching the cross-tab / unrelated-change behaviour that
  // keying on control.interval already gets right.
  const [rejectedInterval, setRejectedInterval] = useState(0);
  const { events } = useEventStream();

  useEffect(() => {
    fetchControl().then(setControl).catch((e) => setError(String(e)));
  }, []);

  // A change from another tab (or another client entirely) arrives here as
  // ControlChanged over SSE, not as a response to our own fetch - re-fetch
  // so this tab converges without polling.
  //
  // Checking only the last element of `events` is not enough: the server
  // writes a whole sse.since() batch in one poll, EventSource dispatches
  // those messages within one browser task, and React batches the resulting
  // dispatches into a single render - so a ControlChanged followed by, say,
  // a ScanCompleted in the same batch would leave a non-ControlChanged event
  // at the tail and this effect would never fire. Track a high-water seq
  // instead and scan every event that arrived since the last time this ran.
  //
  // eventReducer resets the feed (to a single element, with a lower seq)
  // when the bus's own seq counter moves backwards - a bot restart. That
  // must not wedge the high-water mark: if the newest seq is lower than what
  // we last saw, this is a new session none of whose events have been
  // scanned yet, so treat the whole (freshly reset) array as new.
  const lastSeenSeqRef = useRef(0);
  useEffect(() => {
    if (events.length === 0) return;
    const latestSeq = events[events.length - 1].seq;
    const sessionReset = latestSeq < lastSeenSeqRef.current;
    const newEvents = sessionReset
      ? events
      : events.filter((e) => e.seq > lastSeenSeqRef.current);
    lastSeenSeqRef.current = latestSeq;
    if (newEvents.some((e) => e.type === "ControlChanged")) {
      fetchControl().then(setControl).catch((e) => setError(String(e)));
    }
  }, [events]);

  // Every write goes through here so the page always renders what the server
  // actually accepted, never what we optimistically hoped it would.
  async function send(patch: Partial<Strategy> & { paused?: boolean }) {
    setError(null);
    try {
      setControl(await patchControl(patch));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // A rejected interval patch leaves control.interval untouched, so the
      // Input's key would not change and the rejected value would stay on
      // screen - see rejectedInterval's declaration above.
      if ("interval" in patch) setRejectedInterval((n) => n + 1);
    }
  }

  async function stop() {
    if (!confirm("Stop the bot and the dashboard?")) return;
    setError(null);
    try {
      await shutdown();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (!control) {
    return <p className="text-sm text-muted-foreground">{error ?? "Loading…"}</p>;
  }

  const toggleAction = (name: string) =>
    send({
      actions: control.strategy.actions.map((rule) =>
        rule.name === name ? { ...rule, enabled: !rule.enabled } : rule,
      ),
    });

  return (
    <div className="flex max-w-xl flex-col gap-4">
      {error ? <p className="rounded-md border border-red-500 p-2 text-sm text-red-500">{error}</p> : null}

      <Card className="flex-row items-center gap-2 p-3">
        <Button variant="outline" onClick={() => send({ paused: !control.paused })}>
          {control.paused ? "Resume" : "Pause"}
        </Button>
        <Button variant="destructive" onClick={() => void stop()}>
          Stop
        </Button>
        <span className="self-center text-sm text-muted-foreground">
          {control.paused ? "paused — scanning, not tapping" : "running"}
        </span>
      </Card>

      <Card className="gap-3 p-3">
        <label className="flex items-center justify-between text-sm">
          Scan interval (s)
          <Input
            key={`${rejectedInterval}-${control.strategy.interval}`}
            type="number" min={0.1} max={3600} step={0.1} defaultValue={control.strategy.interval}
            onBlur={(e) => send({ interval: Number(e.target.value) })}
            className="w-24 text-right"
          />
        </label>

        <label className="flex items-center justify-between text-sm">
          Auto-navigate
          <input type="checkbox" checked={control.strategy.auto_navigate}
                 onChange={(e) => send({ auto_navigate: e.target.checked })} />
        </label>

        <div className="text-sm">
          <div className="mb-1">Affordability</div>
          {["digits", "brightness"].map((name) => {
            const usable = control.affordability_available.includes(name);
            return (
              <label key={name} className={`mr-4 ${usable ? "" : "text-muted-foreground"}`}>
                <input type="radio" name="strategy" checked={control.strategy.affordability === name}
                       disabled={!usable} onChange={() => send({ affordability: name })} />{" "}
                {name}{usable ? "" : " (no atlas)"}
              </label>
            );
          })}
        </div>

        <div className="text-sm">
          <div className="mb-1">Actions</div>
          {control.strategy.actions.map((rule) => (
            <label key={rule.name} className="mr-4 inline-block">
              <input type="checkbox" checked={rule.enabled}
                     onChange={() => toggleAction(rule.name)} />{" "}
              {rule.name}
            </label>
          ))}
        </div>
      </Card>
    </div>
  );
}
