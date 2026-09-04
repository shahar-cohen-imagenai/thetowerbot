"use client";

import { useEffect, useReducer, useRef, useState } from "react";

/** Re-renders on an interval, so clocks derived from a timestamp advance
 *  between the 2s status polls instead of stepping. */
export function useTick(ms: number) {
  const [, bump] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    const id = setInterval(bump, ms);
    return () => clearInterval(id);
  }, [ms]);
}

/** How long the bot has been sitting on the current screen.
 *
 * There is no such field on /api/status - the payload says which screen, never
 * since when - so this counts locally. `exact` is false until we have actually
 * witnessed a transition: on a fresh page load the bot may have been on this
 * screen for an hour, and reporting "for 0:04" then would be a lie. The caller
 * renders the inexact case as "0:04+".
 *
 * A bot parked on one screen for minutes is the failure this exists to catch,
 * and it is the one thing the old status bar could not show at all. */
export function useScreenAge(screen: string | null | undefined): { seconds: number; exact: boolean } {
  const seen = useRef<{ screen: string | null; since: number; exact: boolean }>({
    screen: null,
    since: Date.now(),
    exact: false,
  });

  if (screen != null && screen !== seen.current.screen) {
    seen.current = {
      screen,
      since: Date.now(),
      // The first screen we ever see is not a transition we watched happen.
      exact: seen.current.screen !== null,
    };
  }

  return { seconds: (Date.now() - seen.current.since) / 1000, exact: seen.current.exact };
}

/** Scans per second, from the delta between two samples of a monotonic counter.
 *
 * The raw `scans` total says nothing on its own - it only ever goes up. The
 * rate is what tells you the loop is turning at all, and roughly how fast.
 *
 * Sampled on a timer rather than keyed on the value, which matters more than
 * it looks: a bot that has wedged stops incrementing `scans`, so an effect
 * with `[scans]` as its dependency would stop re-running at exactly the moment
 * the number mattered, and the tile would hold its last healthy rate forever.
 * Sampling unconditionally makes a wedged loop read 0.0/s, which is the truth. */
export function useScanRate(scans: number | null | undefined, sampleMs = 4000): number | null {
  const current = useRef<number | null>(null);
  current.current = scans ?? null;
  const previous = useRef<{ scans: number; at: number } | null>(null);
  const [rate, setRate] = useState<number | null>(null);

  useEffect(() => {
    const sample = () => {
      const value = current.current;
      if (value == null) return;
      const now = Date.now();
      const last = previous.current;
      previous.current = { scans: value, at: now };
      if (!last || now <= last.at) return;
      const perSecond = (value - last.scans) / ((now - last.at) / 1000);
      // A counter that went backwards means the bot restarted under us.
      // Report nothing rather than a negative rate.
      setRate(perSecond < 0 ? null : perSecond);
    };
    const id = setInterval(sample, sampleMs);
    return () => clearInterval(id);
  }, [sampleMs]);

  return rate;
}

/** True for `ms` after `value` changes, so a tile can flash when its number
 *  moves. Deliberately not a counting animation: rolling digits make a number
 *  unreadable during the exact moment someone is reading it. */
export function useChangeFlash(value: unknown, ms = 300): boolean {
  const previous = useRef(value);
  const [on, setOn] = useState(false);

  useEffect(() => {
    if (previous.current === value) return;
    previous.current = value;
    setOn(true);
    const id = setTimeout(() => setOn(false), ms);
    return () => clearTimeout(id);
  }, [value, ms]);

  return on;
}

/** The median of a numeric list, or null when there is nothing to take one of. */
export function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}
