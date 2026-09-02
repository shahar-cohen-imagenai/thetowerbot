import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StrategyEditor } from "./StrategyEditor";
import type { Strategy } from "@/lib/types";

const strategy: Strategy = {
  name: "default",
  actions: [
    { name: "Damage", template: "d.png", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
    { name: "Speed", template: "s.png", enabled: false, threshold: 0.8, brightness_ratio: 0.75 },
  ],
  affordability: "digits",
  interval: 2,
  click_cooldown: 1,
  auto_navigate: false,
  max_runs: null,
  navigation_cooldown: 3,
  screen_confirmations: 2,
};

describe("StrategyEditor", () => {
  it("renders every row in strategy order", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    const rows = screen.getAllByTestId("action-row");
    expect(rows.map((r) => r.getAttribute("data-name"))).toEqual(["Damage", "Speed"]);
  });

  it("shows the row's position, because order is priority", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("moving a row down swaps it with its neighbour", () => {
    const onChange = vi.fn();
    render(<StrategyEditor value={strategy} onChange={onChange} />);
    fireEvent.click(screen.getAllByLabelText("Move down")[0]);
    expect(onChange.mock.calls[0][0].actions.map((a: {name: string}) => a.name)).toEqual([
      "Speed",
      "Damage",
    ]);
  });

  it("cannot move the first row up or the last row down", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    expect(screen.getAllByLabelText("Move up")[0].hasAttribute("disabled")).toBe(true);
    const downs = screen.getAllByLabelText("Move down");
    expect(downs[downs.length - 1].hasAttribute("disabled")).toBe(true);
  });

  it("toggling a row flips only that row's enabled flag", () => {
    const onChange = vi.fn();
    render(<StrategyEditor value={strategy} onChange={onChange} />);
    fireEvent.click(screen.getAllByLabelText("Enabled")[1]);
    const next = onChange.mock.calls[0][0];
    expect(next.actions[1].enabled).toBe(true);
    expect(next.actions[0].enabled).toBe(true);
  });

  it("marks the two fields that only apply on next Start", () => {
    // Without this the page silently lies: you change the value, the server
    // accepts it, and the running bot goes on using the old one.
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    const badges = screen.getAllByText(/applies on next start/i);
    expect(badges.length).toBe(2);
  });

  it("renders an empty max_runs as unlimited", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Max runs").getAttribute("value")).toBe("");
    expect(screen.getByText(/unlimited/i)).toBeTruthy();
  });

  it("greys out an affordability method with no atlas", () => {
    render(
      <StrategyEditor value={strategy} onChange={vi.fn()} available={["brightness"]} />,
    );
    expect(screen.getByLabelText("digits").hasAttribute("disabled")).toBe(true);
  });

  it("disables every input when told to", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} disabled />);
    expect(screen.getByLabelText("Scan interval (s)").hasAttribute("disabled")).toBe(true);
  });
});
