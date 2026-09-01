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

  it("surfaces a failed stop instead of failing silently", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    stopBot.mockRejectedValue(new Error("POST /api/control/stop -> 500"));
    render(<ControlPage />);

    fireEvent.click(await screen.findByText("Stop"));

    expect(stopBot).toHaveBeenCalled();
    await screen.findByText(/POST \/api\/control\/stop -> 500/);
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

  it("shows the server's value, not the rejected one, after an interval patch is refused", async () => {
    // Regression: control.interval is unchanged on a 422 (send() deliberately
    // skips setControl on failure), so a key on control.interval alone never
    // remounts the field and the browser's dirty, rejected value stays on
    // screen - the specific failure this finding named.
    patchControl.mockRejectedValue(new Error("interval: must be between 0.1 and 3600"));
    render(<ControlPage />);

    const getInput = async () =>
      (await screen.findByText("Scan interval (s)")).closest("label")!.querySelector("input")! as HTMLInputElement;

    expect((await getInput()).value).toBe("1.5");
    fireEvent.change(await getInput(), { target: { value: "99999" } });
    fireEvent.blur(await getInput());

    await screen.findByText(/must be between/);
    // Re-query: a forced remount replaces the DOM node.
    expect((await getInput()).value).toBe("1.5");
  });

  it("repaints the interval field once a cross-tab change converges", async () => {
    const { rerender } = render(<ControlPage />);
    const getInput = async () =>
      (await screen.findByText("Scan interval (s)")).closest("label")!.querySelector("input")! as HTMLInputElement;
    expect((await getInput()).value).toBe("1.5");

    fetchControl.mockResolvedValue({ ...baseControl, interval: 5 });
    streamState.events = [
      { type: "ControlChanged", seq: 1, ts: 0, changed: { interval: 5 }, source: "web" },
    ];
    rerender(<ControlPage />);

    await waitFor(async () => expect((await getInput()).value).toBe("5"));
  });

  it("keeps an in-progress, uncommitted edit when an unrelated remote change arrives", async () => {
    const { rerender } = render(<ControlPage />);
    const getInput = async () =>
      (await screen.findByText("Scan interval (s)")).closest("label")!.querySelector("input")! as HTMLInputElement;

    // Typing, not yet blurred - nothing has been sent to the server.
    fireEvent.change(await getInput(), { target: { value: "42" } });

    // An unrelated field changes remotely - interval itself does not, so
    // this must not stomp the still-uncommitted "42".
    fetchControl.mockResolvedValue({ ...baseControl, paused: true });
    streamState.events = [
      { type: "ControlChanged", seq: 1, ts: 0, changed: { paused: true }, source: "web" },
    ];
    rerender(<ControlPage />);

    await screen.findByText("Resume"); // proves the convergence refetch ran
    expect((await getInput()).value).toBe("42");
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

  it("does not re-fetch for an event that is not ControlChanged", async () => {
    const { rerender } = render(<ControlPage />);
    await screen.findByText("Pause");
    expect(fetchControl).toHaveBeenCalledTimes(1);

    streamState.events = [
      { type: "ScanCompleted", seq: 1, ts: 0, screen: "IN_RUN", duration_ms: 90, wallet: null },
    ];
    rerender(<ControlPage />);

    // No waitFor to assert absence: give any (wrongly) pending refetch a
    // task to run in, then confirm it never happened.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchControl).toHaveBeenCalledTimes(1);
  });

  it("re-fetches for a ControlChanged buried in a batch, not just the last event", async () => {
    // Regression: the server writes a whole poll's worth of events in one
    // SSE write, EventSource dispatches them within one browser task, and
    // React batches the resulting state updates into a single render - so
    // this whole batch arrives as one `events` update, same as production.
    const { rerender } = render(<ControlPage />);
    await screen.findByText("Pause");
    expect(fetchControl).toHaveBeenCalledTimes(1);

    streamState.events = [
      { type: "ControlChanged", seq: 1, ts: 0, changed: { paused: true }, source: "web" },
      { type: "ScanCompleted", seq: 2, ts: 0, screen: "IN_RUN", duration_ms: 90, wallet: null },
    ];
    rerender(<ControlPage />);

    await waitFor(() => expect(fetchControl).toHaveBeenCalledTimes(2));
  });
});
