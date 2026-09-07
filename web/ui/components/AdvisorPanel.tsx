"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { SectionCard } from "@/components/ui/section-card";
import { Input } from "@/components/ui/input";
import { fetchAdvisor, importAdvisor, stageAdvisor } from "@/lib/api";
import { errorText } from "@/lib/utils";
import type {
  AdvisorRecommendation,
  AdvisorSnapshot,
  Strategy,
} from "@/lib/types";

const MAX_BYTES = 256 * 1024;
const DAY_SECONDS = 24 * 60 * 60;
const paths = ["all", "health", "damage", "economy"] as const;
const label = (text: string) =>
  text.charAt(0).toUpperCase() + text.slice(1).replace(/_/g, " ");
const number = (value: number | null) =>
  value == null
    ? "Unknown"
    : value.toLocaleString(undefined, { maximumSignificantDigits: 12 });
const time = (value: number | null) =>
  value == null ? "Unknown" : new Date(value * 1000).toLocaleString();

type Props = {
  profile: string;
  value: Strategy;
  onChange: (value: Strategy) => void;
  disabled?: boolean;
};

/** A profile switch resets local files, messages and every in-flight operation. */
export function AdvisorPanel(props: Props) {
  return <ProfileAdvisor key={props.profile} {...props} />;
}

function ProfileAdvisor({ profile, value, onChange, disabled = false }: Props) {
  const [snapshot, setSnapshot] = useState<AdvisorSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [format, setFormat] = useState<"json" | "csv">("json");
  const [filename, setFilename] = useState("advisor.json");
  const [content, setContent] = useState("");
  const [path, setPath] = useState<(typeof paths)[number]>("all");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const alive = useRef(false);
  const request = useRef(0);
  const working = useRef(false);
  const latest = useRef({ value, onChange, disabled });
  useEffect(() => {
    latest.current = { value, onChange, disabled };
  }, [value, onChange, disabled]);

  const refresh = useCallback(async () => {
    if (working.current) return;
    const id = ++request.current;
    setLoading(true);
    setError(null);
    try {
      const next = await fetchAdvisor(profile);
      if (alive.current && id === request.current) {
        if (next.profile !== profile)
          throw new Error(
            "The advisor returned a different profile. Refresh to retry.",
          );
        setSnapshot(next);
      }
    } catch (error) {
      if (alive.current && id === request.current) setError(errorText(error));
    } finally {
      if (alive.current && id === request.current) setLoading(false);
    }
  }, [profile]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    const clock = setInterval(() => setNow(Date.now() / 1000), 60000);
    return () => {
      alive.current = false;
      request.current += 1;
      clearInterval(clock);
    };
  }, [refresh]);

  const source = snapshot?.source;
  const stale =
    !!snapshot?.stale ||
    (!!source &&
      now - Math.min(source.account_snapshot_at, source.exported_at) >
        DAY_SECONDS);
  const missing = snapshot?.missing_inputs ?? [];
  const unavailable = disabled || loading || busy;
  const rows = (snapshot?.recommendations ?? []).filter(
    (row) => path === "all" || row.path === path,
  );

  function begin(): number | null {
    if (disabled || loading || working.current) return null;
    working.current = true;
    setBusy(true);
    setError(null);
    setMessage(null);
    return ++request.current;
  }
  function current(id: number): boolean {
    return alive.current && id === request.current;
  }
  function finish(id: number) {
    if (current(id)) {
      working.current = false;
      setBusy(false);
    }
  }
  async function readFile(file: File) {
    const id = begin();
    if (id === null) return;
    try {
      if (file.size > MAX_BYTES)
        throw new Error("Choose a file no larger than 256 KiB.");
      const extension = file.name.split(".").pop()?.toLowerCase();
      if (extension !== "json" && extension !== "csv")
        throw new Error("Choose a normalized JSON or CSV export.");
      const text = await file.text();
      if (!current(id)) return;
      setContent(text);
      setFilename(file.name);
      setFormat(extension);
      setMessage(
        `Loaded ${file.name}. Review the content, then import recommendations.`,
      );
    } catch (error) {
      if (current(id)) setError(errorText(error));
    } finally {
      finish(id);
    }
  }
  async function importData() {
    const id = begin();
    if (id === null) return;
    try {
      if (!content.trim())
        throw new Error(
          "Choose a file or paste normalized export content first.",
        );
      if (new TextEncoder().encode(content).length > MAX_BYTES)
        throw new Error("Export content must be no larger than 256 KiB.");
      const next = await importAdvisor({ profile, filename, content });
      if (!current(id)) return;
      if (next.profile !== profile)
        throw new Error(
          "The imported recommendations belong to a different profile.",
        );
      setSnapshot(next);
      setNow(Date.now() / 1000);
      setMessage(
        "Recommendations imported locally. Select a supported recommendation to add it to your draft.",
      );
    } catch (error) {
      if (current(id)) setError(errorText(error));
    } finally {
      finish(id);
    }
  }
  async function stage(row: AdvisorRecommendation) {
    if (!snapshot?.import_id || stale || missing.length || !row.can_stage)
      return;
    const id = begin();
    if (id === null) return;
    const submitted = value;
    const fingerprint = JSON.stringify(submitted);
    try {
      const result = await stageAdvisor({
        profile,
        import_id: snapshot.import_id,
        recommendation_id: row.id,
        draft: submitted,
      });
      if (!current(id)) return;
      if (latest.current.disabled) {
        setMessage(
          "A strategy operation is in progress. Add the recommendation again when it finishes.",
        );
        return;
      }
      if (JSON.stringify(latest.current.value) !== fingerprint) {
        setMessage(
          "The strategy draft changed while this recommendation was being prepared. Add it again to use your current draft.",
        );
        return;
      }
      if (result.draft.name !== profile)
        throw new Error(
          "The advisor returned a draft for a different profile.",
        );
      if (result.added) latest.current.onChange(result.draft);
      setMessage(result.message);
    } catch (error) {
      if (current(id)) setError(errorText(error));
    } finally {
      finish(id);
    }
  }

  return (
    <SectionCard
      id="advisor"
      title="Effective Paths advisor"
      contentClassName="flex flex-col gap-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="max-w-2xl text-xs text-muted-foreground">
          Import a normalized recommendation export for profile{" "}
          <strong>{profile}</strong>. This is a local import workflow; Google
          Sheets syncing is not connected.
        </p>
        <button
          type="button"
          disabled={unavailable}
          onClick={() => void refresh()}
          className="rounded-md border px-3 py-1.5 text-xs transition-colors hover:border-border-strong hover:bg-muted active:translate-y-px disabled:pointer-events-none disabled:opacity-40"
        >
          Refresh advisor
        </button>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
      {message ? (
        <p role="status" className="text-sm">
          {message}
        </p>
      ) : null}
      <details className="rounded-md border p-3 text-xs">
        <summary className="cursor-pointer font-medium">
          Normalized format and example templates
        </summary>
        <div className="mt-3 flex flex-col gap-2 text-muted-foreground">
          <p>
            Native Effective Paths worksheets are not automatically parsed.
            Convert exported recommendations to this normalized JSON or CSV
            format. Example templates contain illustrative data and are never
            imported automatically.
          </p>
          <p>
            JSON schema v1 contains <code>schema_version</code>,{" "}
            <code>source</code>, <code>missing_inputs</code> and{" "}
            <code>recommendations</code>. Source metadata includes name,
            version, account_name, exported_at and account_snapshot_at (Unix
            seconds), with an optional HTTPS URL.
          </p>
          <p>
            Each recommendation includes id, path, system, upgrade, optional
            upgrade_id, current_value, target_value, value_kind (level or stat),
            cost, currency and numeric benefit. Unknown numeric values are{" "}
            <code>null</code>. Levels are never converted into displayed stat
            values.
          </p>
          <p>
            CSV repeats source_name, source_version, source_url, account_name,
            exported_at and account_snapshot_at on each row; missing_inputs uses
            semicolons. Files are limited to 256 KiB and 200 recommendations.
            Formulas are not accepted.
          </p>
          <p className="flex flex-wrap gap-3">
            <a href="/api/advisor/template.json" download className="underline">
              Example JSON template
            </a>
            <a href="/api/advisor/template.csv" download className="underline">
              Example CSV template
            </a>
          </p>
        </div>
      </details>
      <div className="grid gap-3 rounded-md border p-3 md:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs">
          Normalized export file
          <Input
            type="file"
            aria-label="Normalized export file"
            accept=".json,.csv,application/json,text/csv"
            disabled={unavailable}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void readFile(file);
              event.target.value = "";
            }}
          />
        </label>
        <label className="flex items-center gap-2 text-xs">
          Import format
          <select
            aria-label="Import format"
            value={format}
            disabled={unavailable}
            className="rounded-md border bg-background p-2"
            onChange={(event) => {
              const next = event.target.value as "json" | "csv";
              setFormat(next);
              setFilename(`advisor.${next}`);
            }}
          >
            <option value="json">Normalized JSON</option>
            <option value="csv">Normalized CSV</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs md:col-span-2">
          Normalized export content
          <textarea
            aria-label="Normalized export content"
            value={content}
            disabled={unavailable}
            onChange={(event) => setContent(event.target.value)}
            placeholder="Paste your account's normalized export, or choose a file above."
            rows={5}
            className="w-full rounded-md border bg-background p-2 font-mono text-xs"
          />
        </label>
        <div className="flex items-center gap-3 md:col-span-2">
          <button
            type="button"
            disabled={unavailable || !content.trim()}
            onClick={() => void importData()}
            className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/80 active:translate-y-px disabled:pointer-events-none disabled:opacity-40"
          >
            Import recommendations
          </button>
          <span className="text-xs text-muted-foreground">
            {filename} · saves this profile&apos;s local import
          </span>
        </div>
      </div>
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading advisor…</p>
      ) : null}
      {!loading && !source ? (
        <p className="text-sm text-muted-foreground">
          No recommendations imported for this profile. Import your
          account&apos;s results to begin.
        </p>
      ) : null}
      {source ? (
        <>
          <div className="rounded-md border p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="text-sm font-medium">
                {source.name} · {source.version}
              </p>
              <span
                className={`rounded px-2 py-1 text-xs ${stale || missing.length ? "bg-warn-surface text-warn" : "bg-muted"}`}
              >
                {stale
                  ? "Stale · staging blocked"
                  : missing.length
                    ? "Missing inputs · staging blocked"
                    : "Current import"}
              </span>
            </div>
            <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <dt className="text-muted-foreground">Account</dt>
                <dd>{source.account_name}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Account snapshot</dt>
                <dd>{time(source.account_snapshot_at)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Exported</dt>
                <dd>{time(source.exported_at)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Imported locally</dt>
                <dd>{time(snapshot?.imported_at ?? null)}</dd>
              </div>
            </dl>
            {missing.length ? (
              <p className="mt-2 text-xs text-warn">
                Missing inputs: {missing.join(", ")}
              </p>
            ) : null}
            {stale ? (
              <p className="mt-2 text-xs text-warn">
                Import a new export with account data from the last 24 hours
                before adding recommendations.
              </p>
            ) : null}
            {source.url ? (
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-block text-xs underline"
              >
                Open source
              </a>
            ) : null}
          </div>
          <div
            className="flex flex-wrap gap-2"
            aria-label="Recommendation paths"
          >
            {paths.map((item) => (
              <button
                type="button"
                key={item}
                aria-pressed={path === item}
                onClick={() => setPath(item)}
                className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${path === item ? "bg-muted font-semibold" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}
              >
                {label(item)}
              </button>
            ))}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b text-xs text-muted-foreground">
                  <th className="p-2">Recommendation</th>
                  <th className="p-2">Current → target</th>
                  <th className="p-2">Cost</th>
                  <th className="p-2">Benefit from source</th>
                  <th className="p-2">Workshop draft</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b align-top">
                    <td className="p-2">
                      <span className="font-medium">{row.upgrade}</span>
                      <span className="block text-xs text-muted-foreground">
                        {label(row.path)} · {label(row.system)}
                      </span>
                    </td>
                    <td className="whitespace-nowrap p-2 font-mono text-xs">
                      {number(row.current_value)} → {number(row.target_value)}
                      <span className="block font-sans text-muted-foreground">
                        {row.value_kind === "level"
                          ? "Levels"
                          : "Displayed stat values"}
                      </span>
                    </td>
                    <td className="p-2 font-mono text-xs">
                      {number(row.cost)}
                      <span className="block font-sans text-muted-foreground">
                        {row.currency}
                      </span>
                    </td>
                    <td className="p-2 font-mono text-xs">
                      {number(row.benefit)}
                    </td>
                    <td className="p-2">
                      <button
                        type="button"
                        aria-label={`Add ${row.upgrade} to Workshop draft`}
                        disabled={
                          unavailable ||
                          stale ||
                          !!missing.length ||
                          !row.can_stage ||
                          !snapshot?.import_id
                        }
                        onClick={() => void stage(row)}
                        className="whitespace-nowrap rounded-md border px-3 py-1.5 text-xs transition-colors hover:border-border-strong hover:bg-muted active:translate-y-px disabled:pointer-events-none disabled:opacity-40"
                      >
                        Add to Workshop draft
                      </button>
                      {row.blocked_reason ? (
                        <p className="mt-1 max-w-xs text-xs text-muted-foreground">
                          {row.blocked_reason}
                        </p>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!rows.length ? (
              <p className="p-3 text-xs text-muted-foreground">
                No recommendations in this path.
              </p>
            ) : null}
          </div>
          <p className="text-xs text-muted-foreground">
            Supported recommendations append to the Workshop draft after your
            existing priorities. Use Save or Revert on this Strategy page.
            Existing rules, spending limits and arming remain under your
            control.
          </p>
        </>
      ) : null}
    </SectionCard>
  );
}
