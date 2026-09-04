"use client";

import { useEffect, useState } from "react";
import { fetchAutopilot } from "@/lib/api";
import type { AutopilotSnapshot } from "@/lib/types";

/** Read-only polling, with no overlapping requests or post-unmount updates. */
export function useAutopilot() {
  const [snapshot, setSnapshot] = useState<AutopilotSnapshot | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const clock = setInterval(() => setNow(Date.now()), 2000);
    async function refresh() {
      try {
        const next = await fetchAutopilot();
        if (alive) {
          setSnapshot(next);
          setUnreachable(false);
        }
      } catch {
        if (alive) setUnreachable(true);
      } finally {
        if (alive) {
          setNow(Date.now());
          timer = setTimeout(() => void refresh(), 2000);
        }
      }
    }
    void refresh();
    return () => {
      alive = false;
      clearTimeout(timer);
      clearInterval(clock);
    };
  }, []);
  return { snapshot, unreachable, now };
}
