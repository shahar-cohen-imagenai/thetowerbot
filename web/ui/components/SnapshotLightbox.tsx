"use client";

import { useEffect } from "react";
import type { Snapshot } from "@/lib/types";

/**
 * One unknown-screen capture, shown whole.
 *
 * Fit rather than 1:1: these frames are 1080x2400, taller than the screen
 * they are being read on, and the question the gallery exists to answer -
 * "what did the bot hit?" - is answered by the whole frame at once. A 1:1
 * view would put the answer behind a scrollbar.
 *
 * Three ways out, because a full-bleed overlay with one is a trap: the
 * button, the backdrop, and Escape. The image stops its own clicks so that
 * aiming at the picture is not a way to dismiss it.
 */
export function SnapshotLightbox({ shot, onClose }: { shot: Snapshot; onClose: () => void }) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={shot.name}
      onClick={onClose}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-3 bg-black/80 p-4"
    >
      <div className="flex w-full max-w-[90vw] items-center justify-between gap-4 text-sm text-white">
        <span className="truncate font-mono">{shot.name}</span>
        <span className="shrink-0 text-white/70">
          {new Date(shot.ts * 1000).toLocaleString()}
        </span>
        <button
          type="button"
          aria-label="Close"
          onClick={onClose}
          className="shrink-0 rounded border border-white/30 px-2 py-1 leading-none hover:bg-white/10"
        >
          ✕
        </button>
      </div>
      {/* Plain <img> for the same reason SnapshotStrip uses one: these are
          local PNGs off the same FastAPI process, and next/image wants a
          loader. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={shot.url}
        alt={shot.name}
        onClick={(event) => event.stopPropagation()}
        className="max-h-[85vh] max-w-[90vw] rounded border border-white/20 object-contain"
      />
    </div>
  );
}
