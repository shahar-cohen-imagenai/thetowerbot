"use client";

import { useEffect, useRef, useState } from "react";
import { fetchStatus, patchControl, stopBot } from "@/lib/api";
import { checkRuntimeCompatibility, isRuntimeMetadata } from "@/lib/runtimeCompatibility";
import type { RuntimeMetadata, StatusPayload } from "@/lib/types";

const ALL_CAPABILITIES = ["control", "lifecycle", "strategies", "autopilot", "advisor"];
const MODE_LABELS: Record<RuntimeMetadata["readiness"]["mode"], string> = {
  stopped: "Stopped",
  paused: "Paused",
  observing: "Watching",
  automation_enabled: "Automation enabled",
};

export function RuntimeGate({
  children,
  pollIntervalMs = 5_000,
}: {
  children: React.ReactNode;
  pollIntervalMs?: number;
}) {
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [safetyError, setSafetyError] = useState<string | null>(null);
  const requestId = useRef(0);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      const id = ++requestId.current;
      try {
        const next = await fetchStatus();
        if (active && id === requestId.current) {
          setStatus(next);
          setStatusError(false);
        }
      } catch {
        if (active && id === requestId.current) {
          setStatus(null);
          setStatusError(true);
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), pollIntervalMs);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [pollIntervalMs]);

  const compatibility = checkRuntimeCompatibility(status?.runtime);
  const loading = status === null && !statusError;
  const locked = loading || statusError || !compatibility.compatible;
  const runtime = isRuntimeMetadata(status?.runtime) ? status.runtime : null;
  const missingCapabilities = runtime && compatibility.compatible
    ? ALL_CAPABILITIES.filter((capability) => !runtime.capabilities.includes(capability))
    : [];

  const safetyAction = async (action: () => Promise<unknown>) => {
    setSafetyError(null);
    try {
      await action();
    } catch (error) {
      setSafetyError(error instanceof Error ? error.message : "Safety action failed");
    }
  };

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <section
        data-runtime-banner
        aria-label="Runtime readiness"
        className={`w-full border-b px-4 py-3 ${locked ? "border-danger/40 bg-danger-surface" : "border-border bg-card"}`}
      >
        <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
          <div className="min-w-0 flex-1">
            <p className="font-semibold">
              {loading ? "Checking runtime compatibility…" : statusError ? "Runtime status unavailable — controls locked" : locked ? "Runtime mismatch — controls locked" : MODE_LABELS[runtime!.readiness.mode]}
            </p>
            {locked && !loading && (
              <div className="mt-1 text-sm text-muted-foreground">
                {compatibility.reasons.map((reason) => <p key={reason}>{reason}</p>)}
                <p>Restart the backend from this checkout, then rebuild and refresh the dashboard.</p>
              </div>
            )}
            {!locked && runtime!.readiness.reasons.map((reason) => (
              <p key={reason} className="mt-1 text-sm text-muted-foreground">{reason}</p>
            ))}
            {missingCapabilities.length > 0 && (
              <p className="mt-1 text-sm text-warn">
                Some features are unavailable: {missingCapabilities.join(", ")}.
              </p>
            )}
            {safetyError && <p role="alert" className="mt-1 text-sm text-danger">{safetyError}</p>}
          </div>
          {locked && (
            <div className="flex flex-wrap gap-2" aria-label="Emergency controls">
              <button className="rounded-md border border-border-strong px-3 py-2 text-sm transition-colors hover:bg-muted active:translate-y-px" onClick={() => void safetyAction(() => patchControl({ paused: true }))}>
                Pause automation
              </button>
              <button className="rounded-md bg-danger px-3 py-2 text-sm text-danger-foreground transition-colors hover:bg-danger/90 active:translate-y-px" onClick={() => void safetyAction(stopBot)}>
                Stop bot
              </button>
            </div>
          )}
        </div>
        {runtime && (
          <details className="mt-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none">Runtime details</summary>
            <dl className="mt-2 grid min-w-0 grid-cols-1 gap-x-5 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
              <div><dt className="inline font-semibold">Backend revision: </dt><dd className="inline break-all font-mono">{runtime.backend.revision ?? "Unknown"}</dd></div>
              <div><dt className="inline font-semibold">Backend hash: </dt><dd className="inline break-all font-mono">{runtime.backend.source_hash ?? "Unknown"}</dd></div>
              <div><dt className="inline font-semibold">UI hash: </dt><dd className="inline break-all font-mono">{runtime.frontend.source_hash ?? "Unknown"}</dd></div>
              <div><dt className="inline font-semibold">Profile: </dt><dd className="inline">{runtime.profile ?? "None"}</dd></div>
              <div><dt className="inline font-semibold">Device: </dt><dd className="inline break-all font-mono">{runtime.device.serial ?? "Unknown"}</dd></div>
              <div><dt className="inline font-semibold">Game version: </dt><dd className="inline">{runtime.device.game_version ?? "Unknown"}</dd></div>
            </dl>
          </details>
        )}
        {process.env.NEXT_PUBLIC_DEV_UI === "true" && <p className="mt-2 text-xs text-muted-foreground">Development UI</p>}
      </section>
      <fieldset disabled={locked} className="min-w-0 flex-1 border-0 p-0">
        {children}
      </fieldset>
    </div>
  );
}
