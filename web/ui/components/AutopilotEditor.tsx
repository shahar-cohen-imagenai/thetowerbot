"use client";

import { useState } from "react";
import { SectionCard } from "@/components/ui/section-card";
import { NumberField } from "@/components/ui/number-field";
import { Input } from "@/components/ui/input";
import type {
  AutopilotCommand,
  AutopilotPolicy,
  AutopilotPreset,
  AutopilotSnapshot,
  Strategy,
  Upgrade,
  UpgradeRule,
} from "@/lib/types";

export const DEFAULT_AUTOPILOT: AutopilotPolicy = {
  enabled: false,
  preset: "manual",
  rules: [],
  economy_until_wave: 20,
  survival_buffer: 1.2,
  cash_reserve: 0,
  max_scrolls: 8,
};
const categories = ["ALL", "ATTACK", "DEFENSE", "UTILITY"] as const;
const label = (name: string) =>
  name.charAt(0).toUpperCase() + name.slice(1).toLowerCase();
const identity = (name: string) => name.toLowerCase().replace(/[^a-z0-9]/g, "");
const stateLabels: Record<string, string> = {
  unknown: "Unknown",
  locked: "Locked",
  unlocked: "Unlocked",
  maxed: "Maxed",
  unreadable: "Unreadable",
  available: "Available",
  unaffordable: "Insufficient currency",
};

export function AutopilotEditor({
  value,
  onChange,
  catalog,
  presets,
  snapshot,
  disabled = false,
  error,
  onCommand,
  commandPending = false,
}: {
  value: Strategy;
  onChange: (value: Strategy) => void;
  catalog: Upgrade[];
  presets: AutopilotPreset[];
  snapshot: AutopilotSnapshot | null;
  disabled?: boolean;
  error?: string | null;
  onCommand?: (command: AutopilotCommand) => void;
  commandPending?: boolean;
}) {
  const [context, setContext] = useState<"battle" | "workshop">("battle");
  const [category, setCategory] = useState<(typeof categories)[number]>("ALL");
  const [search, setSearch] = useState("");
  const savedPolicy = value.autopilot ?? DEFAULT_AUTOPILOT;
  // The executor falls back to preset rules when the stored rule list is empty.
  // Show that same effective plan before any edit can enable it.
  const policy = savedPolicy.rules.length
    ? savedPolicy
    : {
        ...savedPolicy,
        rules: presets.find((preset) => preset.name === savedPolicy.preset)?.rules ?? [],
      };
  const fresh =
    snapshot?.updated_at != null &&
    Date.now() / 1000 - snapshot.updated_at <= 15;
  const controlsDisabled =
    disabled || commandPending || !snapshot?.can_control;
  const updatePolicy = (patch: Partial<AutopilotPolicy>) =>
    onChange({ ...value, autopilot: { ...policy, ...patch } });
  const matches = (upgrade: Upgrade, name: string) =>
    [upgrade.name, ...upgrade.aliases].some(
      (alias) => identity(alias) === identity(name),
    );
  const workshopExtras: Upgrade[] = value.shopping.workshop
    .filter(
      (row) =>
        !catalog.some(
          (upgrade) =>
            upgrade.category === row.category && matches(upgrade, row.name),
        ),
    )
    .map((row) => ({
      id: `saved:${row.category}:${row.name}`,
      name: row.name,
      category: row.category,
      aliases: [],
      unlock: /unlock/i.test(row.name),
    }));
  const discovered: Upgrade[] = (snapshot?.observations ?? [])
    .filter(
      (row) =>
        row.context.toLowerCase() === context &&
        row.upgrade_id.startsWith("discovered:"),
    )
    .map((row) => ({
      id: row.upgrade_id,
      name: row.name,
      category: row.category,
      aliases: [],
      unlock: false,
    }));
  const rows = [
    ...(context === "battle"
      ? catalog.filter((row) => !row.unlock)
      : [...catalog, ...workshopExtras]),
    ...discovered,
  ];
  const ruleFor = (upgrade: Upgrade): UpgradeRule | undefined =>
    context === "battle"
      ? policy.rules.find((rule) => rule.upgrade_id === upgrade.id)
      : (() => {
          const rule = value.shopping.workshop.find(
            (row) =>
              row.category === upgrade.category && matches(upgrade, row.name),
          );
          return rule
            ? {
                upgrade_id: upgrade.id,
                enabled: rule.enabled,
                target: rule.target,
              }
            : undefined;
        })();
  const active = rows
    .filter((row) => ruleFor(row)?.enabled)
    .sort((a, b) => priority(a) - priority(b));
  function priority(upgrade: Upgrade): number {
    return context === "battle"
      ? policy.rules.findIndex((rule) => rule.upgrade_id === upgrade.id)
      : value.shopping.workshop.findIndex(
          (row) =>
            row.category === upgrade.category && matches(upgrade, row.name),
        );
  }
  function changeRule(upgrade: Upgrade, patch: Partial<UpgradeRule>) {
    if (context === "battle") {
      const existing = policy.rules.find(
        (rule) => rule.upgrade_id === upgrade.id,
      );
      const next = {
        upgrade_id: upgrade.id,
        enabled: false,
        target: null,
        ...existing,
        ...patch,
      };
      updatePolicy({
        rules: existing
          ? policy.rules.map((rule) =>
              rule.upgrade_id === upgrade.id ? next : rule,
            )
          : [...policy.rules, next],
      });
    } else {
      const existing = value.shopping.workshop.find(
        (row) =>
          row.category === upgrade.category && matches(upgrade, row.name),
      );
      const next = {
        name: upgrade.name,
        category: upgrade.category,
        enabled: false,
        target: null,
        ...existing,
        ...("enabled" in patch ? { enabled: patch.enabled! } : {}),
        ...("target" in patch ? { target: patch.target } : {}),
      };
      onChange({
        ...value,
        shopping: {
          ...value.shopping,
          workshop: existing
            ? value.shopping.workshop.map((row) =>
                row === existing ? next : row,
              )
            : [...value.shopping.workshop, next],
        },
      });
    }
  }
  function move(upgrade: Upgrade, delta: number) {
    const from = priority(upgrade);
    if (context === "battle") {
      const rules = [...policy.rules];
      const to = from + delta;
      if (from < 0 || to < 0 || to >= rules.length) return;
      [rules[from], rules[to]] = [rules[to], rules[from]];
      updatePolicy({ rules });
    } else {
      const workshop = [...value.shopping.workshop];
      const to = from + delta;
      if (from < 0 || to < 0 || to >= workshop.length) return;
      [workshop[from], workshop[to]] = [workshop[to], workshop[from]];
      onChange({ ...value, shopping: { ...value.shopping, workshop } });
    }
  }
  const filtered = rows.filter(
    (row) =>
      (category === "ALL" || row.category === category) &&
      [row.name, ...row.aliases].some((name) =>
        name.toLowerCase().includes(search.toLowerCase()),
      ),
  );
  return (
    <SectionCard
      id="purchases"
      title="Purchases · OCR autopilot"
      contentClassName="flex flex-col gap-4"
    >
      <p className="text-xs text-muted-foreground">
        Plan every upgrade here. OCR discovers availability, values and prices
        in game. Changes remain a draft until Save.
      </p>
      {error ? (
        <p role="status" className="text-sm text-warn">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2" aria-label="Purchase context">
        {(["battle", "workshop"] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            aria-pressed={context === tab}
            onClick={() => setContext(tab)}
            className={`rounded-md border px-4 py-2 text-sm transition-colors ${context === tab ? "bg-primary text-primary-foreground" : "bg-muted hover:bg-[color-mix(in_oklch,var(--muted),var(--foreground)_5%)]"}`}
          >
            {label(tab)}
          </button>
        ))}
      </div>
      {context === "battle" ? (
        <div className="grid gap-3 rounded-md border p-3 md:grid-cols-2">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={policy.enabled}
              disabled={disabled}
              onChange={(event) =>
                updatePolicy({ enabled: event.target.checked })
              }
            />
            Enable battle autopilot
          </label>
          <label className="flex items-center justify-between gap-2 text-sm">
            Guide preset
            <select
              aria-label="Guide preset"
              className="rounded-md border bg-background p-2"
              value={policy.preset}
              disabled={disabled}
              onChange={(event) => {
                const preset = presets.find(
                  (row) => row.name === event.target.value,
                );
                if (preset)
                  updatePolicy({
                    preset: preset.name,
                    rules: preset.rules.map((rule) => ({ ...rule })),
                  });
              }}
            >
              {!presets.some((row) => row.name === "manual") ? (
                <option value="manual">Manual</option>
              ) : null}
              {presets.map((preset) => (
                <option key={preset.name} value={preset.name}>
                  {label(preset.name)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center justify-between gap-2 text-sm">
            Run purpose
            <select
              aria-label="Run purpose"
              value={policy.purpose ?? "farm"}
              disabled={disabled}
              className="rounded-md border bg-background p-2"
              onChange={(event) =>
                updatePolicy({
                  purpose: event.target.value as "farm" | "milestone",
                })
              }
            >
              <option value="farm">Farm coins</option>
              <option value="milestone">Push milestones</option>
            </select>
          </label>
          <p className="text-xs text-muted-foreground">
            Farming compares coins per hour across completed runs. Milestone
            intent stays separate; tier changes are made in game.
          </p>
          <NumberField
            label="Economy until wave"
            value={policy.economy_until_wave}
            min={0}
            step={1}
            disabled={disabled}
            onCommit={(economy_until_wave) =>
              updatePolicy({ economy_until_wave })
            }
          />
          <NumberField
            label="Survival buffer"
            value={policy.survival_buffer}
            min={1}
            step={0.1}
            disabled={disabled}
            onCommit={(survival_buffer) => updatePolicy({ survival_buffer })}
          />
          <NumberField
            label="Battle cash reserve"
            value={policy.cash_reserve}
            min={0}
            step={1}
            disabled={disabled}
            onCommit={(cash_reserve) => updatePolicy({ cash_reserve })}
          />
          <p className="text-xs text-muted-foreground">
            {policy.enabled
              ? "Fresh observations and confirmation govern purchases."
              : "Battle autopilot is off. Existing legacy purchase rules remain in use."}
          </p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          Planned unlocks require shopping to be armed, an available coin budget
          and unlock permission. Configure these in{" "}
          <a href="#shopping" className="underline">
            Shopping below
          </a>
          .
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {categories.map((tab) => (
          <button
            type="button"
            key={tab}
            aria-pressed={category === tab}
            className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${category === tab ? "bg-muted font-semibold" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}
            onClick={() => setCategory(tab)}
          >
            {label(tab)}
          </button>
        ))}
        <Input
          type="search"
          aria-label="Search upgrades"
          placeholder="Search upgrades…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="ml-auto max-w-xs"
        />
      </div>
      {context === "battle" && onCommand ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <button
            type="button"
            disabled={controlsDisabled}
            className="rounded-md border px-3 py-2 transition-colors hover:border-border-strong hover:bg-muted active:translate-y-px disabled:pointer-events-none disabled:opacity-40"
            onClick={() => onCommand({ action: "scan" })}
          >
            Scan upgrades
          </button>
          <button
            type="button"
            disabled={controlsDisabled || category === "ALL"}
            className="rounded-md border px-3 py-2 transition-colors hover:border-border-strong hover:bg-muted active:translate-y-px disabled:pointer-events-none disabled:opacity-40"
            onClick={() => {
              if (category !== "ALL")
                onCommand({ action: "category", category });
            }}
          >
            Show {category === "ALL" ? "category" : label(category)} in game
          </button>
          <span className="text-muted-foreground">
            Live commands act immediately; they do not save the strategy.
            {!snapshot?.can_control
              ? " Available while the bot is running and unpaused in battle."
              : ""}
          </span>
        </div>
      ) : null}
      <div className="rounded-md bg-muted/50 p-3 text-xs">
        <strong>Active plan · {context === "battle" ? "cash" : "coins"}</strong>
        <p className="mt-1 text-muted-foreground">
          {active.length
            ? active
                .map((row, index) => `${index + 1}. ${row.name}`)
                .join(" → ")
            : "No upgrades planned yet."}
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b text-xs text-muted-foreground">
              <th className="p-2">Plan</th>
              <th className="p-2">Upgrade</th>
              <th className="p-2">Observed</th>
              <th className="p-2">Price</th>
              <th className="p-2">Target value</th>
              <th className="p-2">Priority</th>
              {context === "battle" && onCommand ? (
                <th className="p-2">Live action</th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {filtered.map((upgrade) => {
              const rule = ruleFor(upgrade);
              const observation = snapshot?.observations.find(
                (row) =>
                  row.upgrade_id === upgrade.id &&
                  row.context.toLowerCase() === context,
              );
              const status = observation?.status ?? "unknown";
              const stale = observation
                ? Date.now() / 1000 - observation.observed_at > 30
                : false;
              const index = priority(upgrade);
              const unrecognized = upgrade.id.startsWith("discovered:");
              const count =
                context === "battle"
                  ? policy.rules.length
                  : value.shopping.workshop.length;
              return (
                <tr
                  key={upgrade.id}
                  data-testid={`upgrade-${upgrade.id}`}
                  className="border-b align-top"
                >
                  <td className="p-2">
                    <input
                      type="checkbox"
                      aria-label={`Plan ${upgrade.name}`}
                      checked={rule?.enabled ?? false}
                      disabled={disabled || unrecognized}
                      onChange={(event) =>
                        changeRule(upgrade, { enabled: event.target.checked })
                      }
                    />
                  </td>
                  <td className="p-2">
                    <span className="font-medium">{upgrade.name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {label(upgrade.category)}
                      {upgrade.unlock ? " · unlock" : ""}
                      {unrecognized
                        ? " · discovered, identity unconfirmed"
                        : ""}
                    </span>
                  </td>
                  <td className="p-2">
                    <span
                      className={
                        status === "available"
                          ? "text-live"
                          : "text-muted-foreground"
                      }
                    >
                      {stateLabels[status] ?? label(status)}
                    </span>
                    <span className="block font-mono text-xs">
                      {observation?.value == null
                        ? "—"
                        : observation.value.toLocaleString()}
                      {stale ? " · stale" : ""}
                    </span>
                  </td>
                  <td className="p-2 font-mono">
                    {observation?.price == null
                      ? "—"
                      : observation.price.toLocaleString()}
                  </td>
                  <td className="p-2">
                    <Input
                      aria-label={`${upgrade.name} target`}
                      type="number"
                      min={0}
                      step="any"
                      placeholder="No cap"
                      key={`${upgrade.id}:${rule?.target ?? ""}`}
                      defaultValue={rule?.target ?? ""}
                      disabled={disabled || unrecognized}
                      onBlur={(event) => {
                        const target =
                          event.target.value === ""
                            ? null
                            : Number(event.target.value);
                        if (
                          target === null ||
                          (Number.isFinite(target) && target >= 0)
                        )
                          changeRule(upgrade, { target });
                      }}
                      className="w-24"
                    />
                  </td>
                  <td className="whitespace-nowrap p-2">
                    <span className="mr-2 font-mono text-xs">
                      {index < 0 ? "—" : index + 1}
                    </span>
                    <button
                      type="button"
                      aria-label={`Move ${upgrade.name} up`}
                      disabled={disabled || index <= 0}
                      onClick={() => move(upgrade, -1)}
                      className="rounded border px-2 transition-colors hover:border-border-strong hover:bg-muted active:translate-y-px disabled:pointer-events-none disabled:opacity-30"
                    >
                      ↑
                    </button>{" "}
                    <button
                      type="button"
                      aria-label={`Move ${upgrade.name} down`}
                      disabled={disabled || index < 0 || index >= count - 1}
                      onClick={() => move(upgrade, 1)}
                      className="rounded border px-2 transition-colors hover:border-border-strong hover:bg-muted active:translate-y-px disabled:pointer-events-none disabled:opacity-30"
                    >
                      ↓
                    </button>
                  </td>
                  {context === "battle" && onCommand ? (
                    <td className="p-2">
                      <button
                        type="button"
                        aria-label={`Buy ${upgrade.name} once`}
                        disabled={
                          controlsDisabled ||
                          !fresh ||
                          unrecognized ||
                          status !== "available" ||
                          observation?.price == null ||
                          !observation ||
                          Date.now() / 1000 - observation.observed_at > 15
                        }
                        className="whitespace-nowrap rounded-md border px-2 py-1 text-xs transition-colors hover:border-border-strong hover:bg-muted active:translate-y-px disabled:pointer-events-none disabled:opacity-40"
                        onClick={() =>
                          onCommand({ action: "buy", upgrade_id: upgrade.id })
                        }
                      >
                        Buy once
                      </button>
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
        {!filtered.length ? (
          <p className="p-4 text-sm text-muted-foreground">
            No upgrades match these filters.
          </p>
        ) : null}
      </div>
      <p className="text-xs text-muted-foreground">
        Unknown means not observed. Locked and unreadable upgrades can be
        planned; the bot waits for verified availability before buying. Targets
        refer to the displayed stat value.
      </p>
      <p className="text-xs text-muted-foreground">
        Guide sources:{" "}
        <a
          className="underline"
          href="https://the-tower.notion.site/Turtle-Strategy-early-game-1bc91383b93f809c81d0e2825cfeac07"
          target="_blank"
          rel="noreferrer"
        >
          Turtle
        </a>{" "}
        ·{" "}
        <a
          className="underline"
          href="https://the-tower.notion.site/Guide-on-Tier-Progression-23f91383b93f8024b142c9163954e218"
          target="_blank"
          rel="noreferrer"
        >
          Tier progression
        </a>{" "}
        ·{" "}
        <a
          className="underline"
          href="https://www.tower-hub.com/wiki/guide/beginner-guide"
          target="_blank"
          rel="noreferrer"
        >
          Beginner guide
        </a>
      </p>
    </SectionCard>
  );
}
