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
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/api/frame" alt="device screen" className="block w-full" />
        {overlay && size
          ? boxes.map((box) => (
              <div
                key={`${box.name}-${box.x}-${box.y}`}
                title={`${box.name} ${box.score.toFixed(3)}`}
                className={`absolute border-2 ${box.tapped ? "border-emerald-400" : "border-amber-400"}`}
                style={{
                  left: `${(box.x / size.width) * 100}%`,
                  top: `${(box.y / size.height) * 100}%`,
                  width: `${(box.w / size.width) * 100}%`,
                  height: `${(box.h / size.height) * 100}%`,
                }}
              />
            ))
          : null}
      </div>
    </div>
  );
}
