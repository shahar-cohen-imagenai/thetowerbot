import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AutopilotEditor, DEFAULT_AUTOPILOT } from "./AutopilotEditor";
import type { AutopilotSnapshot, Strategy, Upgrade } from "@/lib/types";

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

describe("AutopilotEditor", () => {
  it("allows scanning and category navigation before the first OCR observation", () => {
    const onCommand = vi.fn();
    render(<AutopilotEditor {...props} snapshot={{ can_control: true, updated_at: null, observations: [] } as unknown as AutopilotSnapshot} onChange={vi.fn()} onCommand={onCommand} />);
    fireEvent.click(screen.getByRole("button", { name: "Scan upgrades" }));
    expect(onCommand).toHaveBeenCalledWith({ action: "scan" });
    fireEvent.click(screen.getByRole("button", { name: "Defense" }));
    fireEvent.click(screen.getByRole("button", { name: "Show Defense in game" }));
    expect(onCommand).toHaveBeenCalledWith({ action: "category", category: "DEFENSE" });
    expect(screen.getByRole("button", { name: "Buy Defense Absolute once" })).toBeDisabled();
  });
  it("shows the effective preset plan for a saved profile with an empty rules list", () => {
    render(<AutopilotEditor {...props} value={{ ...strategy, autopilot: { ...DEFAULT_AUTOPILOT, preset: "turtle" } }} onChange={vi.fn()} />);
    expect(screen.getByRole("checkbox", { name: "Plan Defense Absolute" })).toBeChecked();
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
