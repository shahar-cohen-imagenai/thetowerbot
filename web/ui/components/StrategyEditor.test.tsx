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
  tap_jitter_px: 8,
  timing_jitter: 0.15,
  tap_delay: 0.12,
  target_speed: null,
  shopping: {
    enabled: false,
    armed: false,
    visit_every_n_runs: 1,
    max_taps_per_visit: 40,
    workshop: [],
    cards: { enabled: false, gem_floor: 40, max_per_visit: 2, batch: "x1" },
  },
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
    // Named "every", so check every one: the page raises this while a save
    // is in flight, and a single control that missed the prop is exactly
    // the edit that would be lost when the response repaints the form.
    const { container } = render(
      <StrategyEditor value={strategy} onChange={vi.fn()} disabled />,
    );
    const controls = Array.from(container.querySelectorAll("input, button"));
    expect(controls.length).toBeGreaterThan(10);
    const live = controls.filter((c) => !c.hasAttribute("disabled"));
    expect(live.map((c) => c.getAttribute("aria-label") ?? c.outerHTML)).toEqual([]);
  });

  it("repaints a row's threshold when the prop changes, e.g. after a Revert", () => {
    // Regression: React only honours `defaultValue` at mount - on later
    // renders it patches the DOM's `value` *attribute* but never the live
    // `.value` property an input actually displays, and the row's own
    // key={row.name} does not change when row.threshold does. So without
    // its own key on the field, a prop change here left the field showing
    // whatever was on screen before, forever. Reading `.value` (not
    // getAttribute) is what makes that gap visible in a test - the
    // attribute updates either way.
    const { rerender } = render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    const getInput = () => screen.getByLabelText("Damage threshold") as HTMLInputElement;
    expect(getInput().value).toBe("0.9");

    const changed: Strategy = {
      ...strategy,
      actions: [{ ...strategy.actions[0], threshold: 0.5 }, strategy.actions[1]],
    };
    rerender(<StrategyEditor value={changed} onChange={vi.fn()} />);

    expect(getInput().value).toBe("0.5");
  });

  it("repaints a row's brightness_ratio when the prop changes", () => {
    const { rerender } = render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    const getInput = () => screen.getByLabelText("Damage brightness") as HTMLInputElement;
    expect(getInput().value).toBe("0.75");

    const changed: Strategy = {
      ...strategy,
      actions: [{ ...strategy.actions[0], brightness_ratio: 0.3 }, strategy.actions[1]],
    };
    rerender(<StrategyEditor value={changed} onChange={vi.fn()} />);

    expect(getInput().value).toBe("0.3");
  });

  it("repaints max_runs when the prop changes", () => {
    // Starting from a set number rather than null/"" on purpose: Base UI's
    // Input treats the empty-to-number transition as a special case and
    // repaints it even without a key, which would make a null-starting
    // test pass whether or not the fix is present. Number-to-number is the
    // transition that actually exercises the bug.
    const withRuns: Strategy = { ...strategy, max_runs: 5 };
    const { rerender } = render(<StrategyEditor value={withRuns} onChange={vi.fn()} />);
    const getInput = () => screen.getByLabelText("Max runs") as HTMLInputElement;
    expect(getInput().value).toBe("5");

    rerender(<StrategyEditor value={{ ...withRuns, max_runs: 12 }} onChange={vi.fn()} />);

    expect(getInput().value).toBe("12");
  });
  it("renders the three jitter fields with their current values", () => {
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    expect((screen.getByLabelText("Tap jitter (px)") as HTMLInputElement).value).toBe("8");
    expect((screen.getByLabelText("Timing jitter (fraction)") as HTMLInputElement).value).toBe("0.15");
    expect((screen.getByLabelText("Tap delay (s)") as HTMLInputElement).value).toBe("0.12");
  });

  it("committing a jitter field reports only that field", () => {
    const onChange = vi.fn();
    render(<StrategyEditor value={strategy} onChange={onChange} />);
    const input = screen.getByLabelText("Tap jitter (px)");

    fireEvent.change(input, { target: { value: "4" } });
    fireEvent.blur(input);

    expect(onChange.mock.calls[0][0].tap_jitter_px).toBe(4);
    expect(onChange.mock.calls[0][0].timing_jitter).toBe(0.15);
  });

  it("zero is an accepted jitter value, because zero means off", () => {
    const onChange = vi.fn();
    render(<StrategyEditor value={strategy} onChange={onChange} />);
    const input = screen.getByLabelText("Tap jitter (px)");

    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.blur(input);

    expect(onChange.mock.calls[0][0].tap_jitter_px).toBe(0);
  });

  it("caps tap jitter at the radius the buy square can absorb", () => {
    // strategy.MAX_TAP_JITTER_PX is PRICE_REGION.h / 2 = 19. The input's max
    // must agree with the server's ceiling, or the dashboard offers a value
    // the PATCH will reject with a 422.
    render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Tap jitter (px)").getAttribute("max")).toBe("19");
  });

  it("repaints a jitter field when the prop changes", () => {
    const { rerender } = render(<StrategyEditor value={strategy} onChange={vi.fn()} />);
    const getInput = () => screen.getByLabelText("Tap delay (s)") as HTMLInputElement;
    expect(getInput().value).toBe("0.12");

    rerender(<StrategyEditor value={{ ...strategy, tap_delay: 0.4 }} onChange={vi.fn()} />);

    expect(getInput().value).toBe("0.4");
  });

  // -- target speed ---------------------------------------------------------

  it("offers only the speeds the server says it can recognise", () => {
    render(
      <StrategyEditor value={strategy} onChange={vi.fn()} speedValues={[1, 2, 3]} />,
    );
    const options = Array.from(
      (screen.getByLabelText("Target speed") as HTMLSelectElement).options,
    ).map((o) => o.value);

    // "" is the leave-it-alone option, which is not a speed and so is never
    // in the server's list.
    expect(options).toEqual(["", "1", "2", "3"]);
  });

  it("sets a target speed as a number, not the select's string", () => {
    const onChange = vi.fn();
    render(
      <StrategyEditor value={strategy} onChange={onChange} speedValues={[1, 2]} />,
    );

    fireEvent.change(screen.getByLabelText("Target speed"), { target: { value: "2" } });

    expect(onChange.mock.calls[0][0].target_speed).toBe(2);
  });

  it("clears the target speed back to leaving the speed alone", () => {
    const onChange = vi.fn();
    render(
      <StrategyEditor
        value={{ ...strategy, target_speed: 2 }} onChange={onChange} speedValues={[1, 2]}
      />,
    );

    fireEvent.change(screen.getByLabelText("Target speed"), { target: { value: "" } });

    expect(onChange.mock.calls[0][0].target_speed).toBeNull();
  });
});
