"use client";

import { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { fetchStats } from "@/lib/api";
import type { StatsPayload } from "@/lib/types";

// dataviz skill: chart surface/border tokens, so the Tooltip reads correctly
// in both themes instead of Recharts' unstyled white default.
const TOOLTIP_STYLE = {
  contentStyle: {
    background: "var(--popover)",
    color: "var(--popover-foreground)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-md)",
    fontSize: 12,
  },
  labelStyle: { color: "var(--muted-foreground)" },
  cursor: { stroke: "var(--border)" },
};

// One series per panel here, so one hue (the palette's slot 1) does
// identity work for all four charts - it reads as one system rather than
// four unrelated colors, and a single series needs no legend box (the
// panel title already names what's plotted).
const SERIES_COLOR = "var(--chart-1)";

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border p-3">
      <h2 className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">{title}</h2>
      {note ? <p className="mb-2 text-xs text-muted-foreground">{note}</p> : null}
      <div className="h-64">{children}</div>
    </section>
  );
}

// An explicit "nothing here" state for a single panel, distinct from a chart
// with real zero-valued bars - a blank axis with no bars would otherwise read
// as ambiguous ("no data" vs "still loading"), so panels that can be empty
// even while other stored runs exist say so directly.
function EmptyPanel({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">{children}</div>;
}

export default function StatsPage() {
  const [stats, setStats] = useState<StatsPayload | null>(null);
  useEffect(() => { fetchStats().then(setStats).catch(() => setStats(null)); }, []);

  if (!stats) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!stats.runs.length) {
    // An explicit empty state, never zeros that look like real data.
    return <p className="text-sm text-muted-foreground">No stored runs yet. (Running with --no-store?)</p>;
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Wave per run">
        <ResponsiveContainer>
          <LineChart data={stats.runs}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis dataKey="id" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip {...TOOLTIP_STYLE} />
            <Line
              type="monotone" dataKey="wave" dot={false}
              stroke={SERIES_COLOR} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
            />
          </LineChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Run length (s)">
        <ResponsiveContainer>
          <LineChart data={stats.runs}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis dataKey="id" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip {...TOOLTIP_STYLE} />
            <Line
              type="monotone" dataKey="duration" dot={false}
              stroke={SERIES_COLOR} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
            />
          </LineChart>
        </ResponsiveContainer>
      </Panel>

      <Panel
        title="Taps by action"
        note="Older runs logged the template filename (e.g. upgrade_damage.png); newer ones log the action name (e.g. Damage) - both can appear here."
      >
        {stats.taps.length ? (
          <ResponsiveContainer>
            <BarChart data={stats.taps}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
              <XAxis dataKey="action" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip {...TOOLTIP_STYLE} />
              <Bar dataKey="count" fill={SERIES_COLOR} radius={[4, 4, 0, 0]} maxBarSize={24} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <EmptyPanel>No taps recorded yet.</EmptyPanel>
        )}
      </Panel>

      <Panel title="Events by screen">
        {stats.screens.length ? (
          <ResponsiveContainer>
            <BarChart data={stats.screens}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
              <XAxis dataKey="screen" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip {...TOOLTIP_STYLE} />
              <Bar dataKey="count" fill={SERIES_COLOR} radius={[4, 4, 0, 0]} maxBarSize={24} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <EmptyPanel>No screen events recorded yet.</EmptyPanel>
        )}
      </Panel>
    </div>
  );
}
