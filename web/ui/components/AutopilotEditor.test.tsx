import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AutopilotEditor, DEFAULT_AUTOPILOT } from "./AutopilotEditor";
import type {
  AutopilotPreset,
  AutopilotSnapshot,
  Strategy,
  Upgrade,
} from "@/lib/types";

const catalog: Upgrade[] = [
  {
    id: "damage",
    name: "Damage",
    category: "ATTACK",
    aliases: [],
    unlock: false,
  },
  {
    id: "defense_absolute",
    name: "Defense Absolute",
    category: "DEFENSE",
    aliases: [],
    unlock: false,
  },
  {
    id: "cash_bonus",
    name: "Cash Bonus",
    category: "UTILITY",
    aliases: [],
    unlock: false,
  },
];
const strategy = {
  name: "default",
  autopilot: DEFAULT_AUTOPILOT,
  shopping: { workshop: [], enabled: false, armed: false },
} as unknown as Strategy;
const props = {
  value: strategy,
  catalog,
  presets: [
    {
      name: "turtle",
      rules: [{ upgrade_id: "defense_absolute", enabled: true }],
    },
  ],
  snapshot: null,
};
const completePreset: AutopilotPreset = {
  name: "turtle",
  rules: [{ upgrade_id: "defense_absolute", enabled: true, target: null }],
  workshop: [
    { name: "Cash Bonus", category: "UTILITY", enabled: true, target: 5 },
  ],
  description: "Economy and defense across a run and between runs.",
  notes: ["Guide levels are not displayed stat values."],
  sources: [
    { title: "Turtle guide", url: "https://the-tower.notion.site/turtle" },
  ],
};

describe("AutopilotEditor", () => {
  it("applies both plans from Workshop while preserving all automation and spending controls", () => {
    const onChange = vi.fn();
    const value: Strategy = {
      ...strategy,
      interval: 7,
      autopilot: {
        ...DEFAULT_AUTOPILOT,
        enabled: true,
        economy_until_wave: 30,
        cash_reserve: 12,
      },
      shopping: {
        enabled: true,
        armed: true,
        coin_budget: 400,
        coin_reserve: 90,
        allow_unlocks: true,
        visit_every_n_runs: 3,
        max_taps_per_visit: 8,
        workshop: [],
        cards: {
          enabled: true,
          gem_floor: 100,
          max_per_visit: 2,
          batch: "x10",
        },
      },
    };
    render(
      <AutopilotEditor
        {...props}
        value={value}
        presets={[completePreset]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Workshop" }));
    fireEvent.change(screen.getByLabelText("Guide preset"), {
      target: { value: "turtle" },
    });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({
      ...value,
      autopilot: {
        ...value.autopilot,
        preset: "turtle",
        rules: completePreset.rules,
      },
      shopping: { ...value.shopping, workshop: completePreset.workshop },
    });
  });
  it("reapplies the current build to an older profile with no Workshop plan", () => {
    const onChange = vi.fn();
    const value = {
      ...strategy,
      autopilot: {
        ...DEFAULT_AUTOPILOT,
        preset: "turtle",
        rules: completePreset.rules,
      },
    };
    render(
      <AutopilotEditor
        {...props}
        value={value}
        presets={[completePreset]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Workshop" }));
    expect(screen.getByText(completePreset.description!)).toBeTruthy();
    expect(screen.getByText(completePreset.notes![0])).toBeTruthy();
    expect(screen.getByRole("link", { name: "Turtle guide" })).toHaveAttribute(
      "href",
      completePreset.sources![0].url,
    );
    expect(screen.getByText(/Shopping off/)).toBeTruthy();
    expect(screen.getByText(/Budget 0/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reapply preset" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        shopping: { ...value.shopping, workshop: completePreset.workshop },
      }),
    );
  });
  it("retains both plans when switching to Manual", () => {
    const onChange = vi.fn();
    const value = {
      ...strategy,
      autopilot: {
        ...DEFAULT_AUTOPILOT,
        preset: "turtle",
        rules: completePreset.rules,
      },
      shopping: { ...strategy.shopping, workshop: completePreset.workshop! },
    };
    const view = render(
      <AutopilotEditor
        {...props}
        value={value}
        presets={[completePreset]}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText("Guide preset"), {
      target: { value: "manual" },
    });
    const expected = {
      ...value,
      autopilot: { ...value.autopilot, preset: "manual" },
    };
    expect(onChange).toHaveBeenCalledWith(expected);
    view.rerender(
      <AutopilotEditor
        {...props}
        value={expected}
        presets={[completePreset]}
        onChange={onChange}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Reapply preset" }),
    ).toBeDisabled();
  });
  it("preserves Workshop plans when an older server only supplies battle presets", () => {
    const onChange = vi.fn();
    const value = {
      ...strategy,
      shopping: { ...strategy.shopping, workshop: completePreset.workshop! },
    };
    render(<AutopilotEditor {...props} value={value} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Guide preset"), {
      target: { value: "turtle" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ shopping: value.shopping }),
    );
  });
  it("allows scanning and category navigation before the first OCR observation", () => {
    const onCommand = vi.fn();
    render(
      <AutopilotEditor
        {...props}
        snapshot={
          {
            can_control: true,
            updated_at: null,
            observations: [],
          } as unknown as AutopilotSnapshot
        }
        onChange={vi.fn()}
        onCommand={onCommand}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Scan upgrades" }));
    expect(onCommand).toHaveBeenCalledWith({ action: "scan" });
    fireEvent.click(screen.getByRole("button", { name: "Defense" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Show Defense in game" }),
    );
    expect(onCommand).toHaveBeenCalledWith({
      action: "category",
      category: "DEFENSE",
    });
    expect(
      screen.getByRole("button", { name: "Buy Defense Absolute once" }),
    ).toBeDisabled();
  });
  it("shows the effective preset plan for a saved profile with an empty rules list", () => {
    render(
      <AutopilotEditor
        {...props}
        value={{
          ...strategy,
          autopilot: { ...DEFAULT_AUTOPILOT, preset: "turtle" },
        }}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("checkbox", { name: "Plan Defense Absolute" }),
    ).toBeChecked();
  });
  it("shows unseen upgrades as unknown and allows planning without claiming a purchase", () => {
    const onChange = vi.fn();
    render(<AutopilotEditor {...props} onChange={onChange} />);
    const row = screen.getByTestId("upgrade-damage");
    expect(within(row).getByText("Unknown")).toBeTruthy();
    fireEvent.click(within(row).getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        autopilot: expect.objectContaining({
          enabled: false,
          rules: [{ upgrade_id: "damage", enabled: true, target: null }],
        }),
      }),
    );
    expect(screen.queryByRole("button", { name: /buy now/i })).toBeNull();
  });
  it("filters the catalog by category and search", () => {
    render(<AutopilotEditor {...props} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Defense" }));
    expect(screen.getByTestId("upgrade-defense_absolute")).toBeTruthy();
    expect(screen.queryByTestId("upgrade-damage")).toBeNull();
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "missing" },
    });
    expect(screen.getByText("No upgrades match these filters.")).toBeTruthy();
  });
  it("applies presets only to the draft, preserving disabled automation", () => {
    const onChange = vi.fn();
    render(<AutopilotEditor {...props} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Guide preset"), {
      target: { value: "turtle" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        autopilot: expect.objectContaining({
          enabled: false,
          preset: "turtle",
          rules: props.presets[0].rules,
        }),
      }),
    );
  });
  it("preserves unknown saved Workshop rows", () => {
    render(
      <AutopilotEditor
        {...props}
        value={{
          ...strategy,
          shopping: {
            ...strategy.shopping,
            workshop: [
              { name: "Special Unlock", category: "UTILITY", enabled: true },
            ],
          },
        }}
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Workshop" }));
    fireEvent.click(screen.getByRole("button", { name: "Utility" }));
    expect(screen.getAllByText("Special Unlock").length).toBeGreaterThan(0);
    expect(screen.getByText(/Planned unlocks require/)).toBeTruthy();
  });
  it("shows explicit locks separately and displays unrecognized OCR without enabling it", () => {
    const snapshot = {
      observations: [
        {
          upgrade_id: "damage",
          context: "battle",
          category: "ATTACK",
          name: "Damage",
          status: "locked",
          price: null,
          value: null,
          observed_at: Date.now() / 1000,
        },
        {
          upgrade_id: "discovered:unrecognized",
          context: "battle",
          category: "UTILITY",
          name: "Unrecognized OCR",
          status: "available",
          price: 10,
          value: 2,
          observed_at: Date.now() / 1000,
        },
      ],
    } as AutopilotSnapshot;
    render(
      <AutopilotEditor {...props} snapshot={snapshot} onChange={vi.fn()} />,
    );
    const row = screen.getByTestId("upgrade-damage");
    expect(within(row).getByText("Locked")).toBeTruthy();
    expect(within(row).getByRole("checkbox")).not.toBeDisabled();
    expect(
      screen.getByRole("checkbox", { name: "Plan Unrecognized OCR" }),
    ).toBeDisabled();
  });
  it("changes a stat target and retains every other rule", () => {
    const onChange = vi.fn();
    const value = {
      ...strategy,
      autopilot: {
        ...DEFAULT_AUTOPILOT,
        rules: [
          { upgrade_id: "damage", enabled: true },
          { upgrade_id: "cash_bonus", enabled: true, target: 4 },
        ],
      },
    };
    render(<AutopilotEditor {...props} value={value} onChange={onChange} />);
    fireEvent.blur(screen.getByLabelText("Damage target"), {
      target: { value: "25" },
    });
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        autopilot: expect.objectContaining({
          rules: [
            { upgrade_id: "damage", enabled: true, target: 25 },
            value.autopilot.rules[1],
          ],
        }),
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Move Cash Bonus up" }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        autopilot: expect.objectContaining({
          rules: [value.autopilot.rules[1], value.autopilot.rules[0]],
        }),
      }),
    );
  });
  it("only queues explicit fresh available purchases and never saves a draft", () => {
    const now = Date.now() / 1000;
    const onCommand = vi.fn();
    const onChange = vi.fn();
    const snapshot = {
      can_control: true,
      updated_at: now,
      observations: [
        {
          upgrade_id: "damage",
          context: "battle",
          category: "ATTACK",
          name: "Damage",
          status: "available",
          price: 10,
          value: 2,
          observed_at: now,
        },
        {
          upgrade_id: "cash_bonus",
          context: "battle",
          category: "UTILITY",
          name: "Cash Bonus",
          status: "available",
          price: 10,
          value: 2,
          observed_at: now - 60,
        },
      ],
    } as AutopilotSnapshot;
    render(
      <AutopilotEditor
        {...props}
        snapshot={snapshot}
        onChange={onChange}
        onCommand={onCommand}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Buy Cash Bonus once" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Buy Defense Absolute once" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Buy Damage once" }));
    expect(onCommand).toHaveBeenCalledWith({
      action: "buy",
      upgrade_id: "damage",
    });
    expect(onChange).not.toHaveBeenCalled();
  });
});
