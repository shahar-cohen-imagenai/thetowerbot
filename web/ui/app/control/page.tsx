"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { fetchControl, patchControl, stopBot } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";
import type { ControlPayload } from "@/lib/types";

export default function ControlPage() {
  const [control, setControl] = useState<ControlPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { events } = useEventStream();

  useEffect(() => {
    fetchControl().then(setControl).catch((e) => setError(String(e)));
  }, []);

  // A change from another tab (or another client entirely) arrives here as
  // ControlChanged over SSE, not as a response to our own fetch - re-fetch
  // so this tab converges without polling.
  useEffect(() => {
    if (events[events.length - 1]?.type === "ControlChanged") {
      fetchControl().then(setControl).catch((e) => setError(String(e)));
    }
  }, [events]);

  // Every write goes through here so the page always renders what the server
  // actually accepted, never what we optimistically hoped it would.
  async function send(patch: Partial<ControlPayload>) {
    setError(null);
    try {
      setControl(await patchControl(patch));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (!control) {
    return <p className="text-sm text-muted-foreground">{error ?? "Loading…"}</p>;
  }

  const toggleAction = (name: string) =>
    send({
      enabled_actions: control.enabled_actions.includes(name)
        ? control.enabled_actions.filter((a) => a !== name)
        : [...control.enabled_actions, name],
    });

  return (
    <div className="flex max-w-xl flex-col gap-4">
      {error ? <p className="rounded-md border border-red-500 p-2 text-sm text-red-500">{error}</p> : null}

      <Card className="flex-row items-center gap-2 p-3">
        <Button variant="outline" onClick={() => send({ paused: !control.paused })}>
          {control.paused ? "Resume" : "Pause"}
        </Button>
        <Button
          variant="destructive"
          onClick={() => { if (confirm("Stop the bot and the dashboard?")) void stopBot(); }}
        >
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
            type="number" min={0.1} max={3600} step={0.1} defaultValue={control.interval}
            onBlur={(e) => send({ interval: Number(e.target.value) })}
            className="w-24 text-right"
          />
        </label>

        <label className="flex items-center justify-between text-sm">
          Auto-navigate
          <input type="checkbox" checked={control.auto_navigate}
                 onChange={(e) => send({ auto_navigate: e.target.checked })} />
        </label>

        <div className="text-sm">
          <div className="mb-1">Affordability</div>
          {["digits", "brightness"].map((name) => {
            const usable = control.strategies_available.includes(name);
            return (
              <label key={name} className={`mr-4 ${usable ? "" : "text-muted-foreground"}`}>
                <input type="radio" name="strategy" checked={control.strategy === name}
                       disabled={!usable} onChange={() => send({ strategy: name })} />{" "}
                {name}{usable ? "" : " (no atlas)"}
              </label>
            );
          })}
        </div>

        <div className="text-sm">
          <div className="mb-1">Actions</div>
          {control.actions.map((name) => (
            <label key={name} className="mr-4 inline-block">
              <input type="checkbox" checked={control.enabled_actions.includes(name)}
                     onChange={() => toggleAction(name)} />{" "}
              {name}
            </label>
          ))}
        </div>
      </Card>
    </div>
  );
}
