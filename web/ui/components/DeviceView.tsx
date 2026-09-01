"use client";

import { useState } from "react";
import type { MatchBox } from "@/lib/types";

export function DeviceView({
  boxes,
  size,
}: {
  boxes: MatchBox[];
  size: { width: number; height: number } | null;
}) {
  const [overlay, setOverlay] = useState(true);

  return (
    <div className="flex flex-col gap-2">
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input type="checkbox" checked={overlay} onChange={(e) => setOverlay(e.target.checked)} />
        show matches
      </label>

      {/* The boxes are absolutely positioned in percentages of the frame's own
          pixel dimensions, so the image can be any size on screen and the
          overlay follows it - no coordinate maths in two languages. */}
      <div className="relative w-full max-w-xs overflow-hidden rounded-lg border">
        {size ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/api/frame" alt="device screen" className="block w-full" />
            {overlay
              ? boxes.map((box) => (
                  <div key={`${box.name}-${box.x}-${box.y}`}>
                    <div
                      title={`${box.name} ${box.score.toFixed(3)}`}
                      className={`absolute border-2 ${box.tapped ? "border-emerald-400" : "border-amber-400"}`}
                      style={{
                        left: `${(box.x / size.width) * 100}%`,
                        top: `${(box.y / size.height) * 100}%`,
                        width: `${(box.w / size.width) * 100}%`,
                        height: `${(box.h / size.height) * 100}%`,
                      }}
                    />
                    {/* The label itself is a button - tapping it opens an
                        info panel instead of buying anything - so the real
                        tap lands on the buy square beside it, at (tap_x,
                        tap_y). Drawn as a small dot, visually distinct from
                        the box outline, so it reads as "here", not as
                        another rectangle. */}
                    <div
                      title={`${box.name} tap point`}
                      className="absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white bg-sky-400"
                      style={{
                        left: `${(box.tap_x / size.width) * 100}%`,
                        top: `${(box.tap_y / size.height) * 100}%`,
                      }}
                    />
                  </div>
                ))
              : null}
          </>
        ) : (
          // size is null until the first publish() - every other panel on
          // this page has something to say before its first data arrives;
          // this was the one bare blank spot.
          <p className="aspect-[9/16] p-4 text-sm text-muted-foreground">
            Waiting for the first frame…
          </p>
        )}
      </div>
    </div>
  );
}
