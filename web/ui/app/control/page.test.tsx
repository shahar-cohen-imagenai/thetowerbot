import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe as group, expect, it, vi } from "vitest";
import ControlPage from "./page";
import type { BotEvent, ControlPayload } from "@/lib/types";

// vi.mock factories are hoisted above module-level declarations, so the
// vi.fn()s they need to share with the tests are created through
// vi.hoisted() rather than referenced as outer consts (see app/page.test.tsx
// for the alternative - fixtures inlined in the factory itself - which
// doesn't work here because these mocks need per-test return values).
const { fetchControl, patchControl, stopBot } = vi.hoisted(() => ({
  fetchControl: vi.fn(),
  patchControl: vi.fn(),
  stopBot: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ fetchControl, patchControl, stopBot }));

const { streamState } = vi.hoisted(() => ({
  streamState: { events: [] as BotEvent[], connected: true },
}));

vi.mock("@/lib/useEventStream", () => ({
  useEventStream: () => streamState,
}));

const baseControl: ControlPayload = {
  paused: false,
  interval: 1.5,
  auto_navigate: false,
  strategy: "digits",
  enabled_actions: ["Damage"],
  actions: ["Damage", "Health"],
  // "digits" itself is unavailable here - no glyph atlas built.
  strategies_available: ["brightness"],
};

beforeEach(() => {
  vi.clearAllMocks();
  fetchControl.mockResolvedValue(baseControl);
  streamState.events = [];
});

group("ControlPage", () => {
  it("sends the right PATCH on a setting toggle and renders the server's returned state", async () => {
    patchControl.mockResolvedValue({ ...baseControl, paused: true });
    render(<ControlPage />);

    fireEvent.click(await screen.findByText("Pause"));

    expect(patchControl).toHaveBeenCalledWith({ paused: true });
    // Rendered from patchControl's response, not an optimistic flip made
    // before the server answered.
    await screen.findByText("Resume");
    expect(screen.getByText(/paused — scanning, not tapping/)).toBeDefined();
  });

  it("surfaces the server's reason on a rejected patch and leaves the displayed state unchanged", async () => {
    patchControl.mockRejectedValue(new Error("auto_navigate: nope"));
    render(<ControlPage />);

    const checkbox = (await screen.findByText("Auto-navigate")).closest("label")!.querySelector("input")! as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    fireEvent.click(checkbox);

    await screen.findByText(/nope/);
    expect(checkbox.checked).toBe(false);
  });

  it("renders a strategy with no atlas as disabled rather than silently inert", async () => {
    render(<ControlPage />);

    const digits = (await screen.findByRole("radio", { name: /digits/ })) as HTMLInputElement;
    expect(digits.disabled).toBe(true);
    expect(screen.getByText(/digits \(no atlas\)/)).toBeDefined();

    const brightness = screen.getByRole("radio", { name: /brightness/ }) as HTMLInputElement;
    expect(brightness.disabled).toBe(false);
  });

  it("re-fetches control state when a ControlChanged event arrives over SSE", async () => {
    const { rerender } = render(<ControlPage />);
    await screen.findByText("Pause");
    expect(fetchControl).toHaveBeenCalledTimes(1);

    streamState.events = [
      { type: "ControlChanged", seq: 1, ts: 0, changed: { paused: true }, source: "web" },
    ];
    rerender(<ControlPage />);

    await waitFor(() => expect(fetchControl).toHaveBeenCalledTimes(2));
  });
});
