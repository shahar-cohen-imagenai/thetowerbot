import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ControlPage from "./page";

const strategy = {
  name: "default",
  actions: [
    { name: "Damage", template: "d.png", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
    { name: "Speed", template: "s.png", enabled: false, threshold: 0.9, brightness_ratio: 0.75 },
  ],
  affordability: "digits",
  interval: 2,
  click_cooldown: 1,
  auto_navigate: true,
  max_runs: null,
  navigation_cooldown: 3,
  screen_confirmations: 2,
};

const api = vi.hoisted(() => ({
  fetchControl: vi.fn(),
  patchControl: vi.fn(),
  fetchStatus: vi.fn(),
  startBot: vi.fn(),
  stopBot: vi.fn(),
  shutdown: vi.fn(),
}));
vi.mock("@/lib/api", () => api);

const sync = vi.hoisted(() => ({ onChange: null as null | (() => void) }));
vi.mock("@/lib/useControlSync", () => ({
  useControlSync: (cb: () => void) => {
    sync.onChange = cb;
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  sync.onChange = null;
  api.fetchControl.mockResolvedValue({
    paused: false, strategy, affordability_available: ["digits", "brightness"],
  });
  api.fetchStatus.mockResolvedValue({ bot: { running: false, since: null, error: null } });
  api.startBot.mockResolvedValue({ running: true, since: 1, error: null });
  api.stopBot.mockResolvedValue({ running: false, since: null, error: null });
  api.patchControl.mockImplementation((p: Record<string, unknown>) =>
    Promise.resolve({ paused: !!p.paused, strategy, affordability_available: [] }),
  );
});

describe("ControlPage", () => {
  it("offers Start when the bot is stopped", async () => {
    render(<ControlPage />);
    await waitFor(() => expect(screen.getByText("Start")).toBeTruthy());
    expect(screen.queryByText("Stop bot")).toBeNull();
  });

  it("offers Stop when the bot is running", async () => {
    api.fetchStatus.mockResolvedValue({ bot: { running: true, since: 1, error: null } });
    render(<ControlPage />);
    await waitFor(() => expect(screen.getByText("Stop bot")).toBeTruthy());
    expect(screen.queryByText("Start")).toBeNull();
  });

  it("Start calls the bot route, not the shutdown route", async () => {
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Start"));
    fireEvent.click(screen.getByText("Start"));
    await waitFor(() => expect(api.startBot).toHaveBeenCalled());
    expect(api.shutdown).not.toHaveBeenCalled();
  });

  it("shows a dead emulator's message instead of failing silently", async () => {
    api.startBot.mockRejectedValue(new Error("no emulator at 127.0.0.1:5555"));
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Start"));
    fireEvent.click(screen.getByText("Start"));
    await waitFor(() => expect(screen.getByText(/no emulator/)).toBeTruthy());
  });

  it("disables Pause when the bot is stopped, so Resume can't contradict the status text", async () => {
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Pause"));
    expect(screen.getByText("Pause").hasAttribute("disabled")).toBe(true);
  });

  it("enables Pause once the bot is running", async () => {
    api.fetchStatus.mockResolvedValue({ bot: { running: true, since: 1, error: null } });
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Pause"));
    expect(screen.getByText("Pause").hasAttribute("disabled")).toBe(false);
  });

  it("pause is a control patch, not a lifecycle call", async () => {
    api.fetchStatus.mockResolvedValue({ bot: { running: true, since: 1, error: null } });
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Pause"));
    fireEvent.click(screen.getByText("Pause"));
    await waitFor(() => expect(api.patchControl).toHaveBeenCalledWith({ paused: true }));
    expect(api.stopBot).not.toHaveBeenCalled();
  });

  it("renders the server's returned pause state, not an optimistic flip", async () => {
    api.fetchStatus.mockResolvedValue({ bot: { running: true, since: 1, error: null } });
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Pause"));
    fireEvent.click(screen.getByText("Pause"));
    // Rendered from patchControl's response, not a flip made before the
    // server answered.
    await waitFor(() => expect(screen.getByText("Resume")).toBeTruthy());
    expect(screen.getByText(/paused — scanning, not tapping/)).toBeTruthy();
  });

  it("shows a failed shutdown instead of failing silently", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    // The message the real shutdown() throws, route and all - so a mock
    // that has drifted from lib/api.ts is visible here rather than passing
    // happily against a route nobody serves.
    api.shutdown.mockRejectedValue(new Error("POST /api/shutdown -> 500"));
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Shut down"));
    fireEvent.click(screen.getByText("Shut down"));
    await waitFor(() => expect(api.shutdown).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/POST \/api\/shutdown -> 500/)).toBeTruthy());
  });

  it("surfaces the server's reason on a rejected pause patch and leaves the displayed state unchanged", async () => {
    api.fetchStatus.mockResolvedValue({ bot: { running: true, since: 1, error: null } });
    api.patchControl.mockRejectedValue(new Error("control locked: another client is editing"));
    render(<ControlPage />);
    await waitFor(() => screen.getByText("Pause"));
    fireEvent.click(screen.getByText("Pause"));
    await waitFor(() =>
      expect(screen.getByText(/control locked: another client is editing/)).toBeTruthy(),
    );
    // Still "Pause", not "Resume" - a rejected patch must not toggle the
    // button as if the server had accepted it.
    expect(screen.getByText("Pause")).toBeTruthy();
    expect(screen.queryByText("Resume")).toBeNull();
  });

  it("re-fetches control when useControlSync reports another tab changed it", async () => {
    render(<ControlPage />);
    await waitFor(() => expect(api.fetchControl).toHaveBeenCalledTimes(1));
    expect(sync.onChange).not.toBeNull();
    api.fetchControl.mockClear();
    sync.onChange!();
    await waitFor(() => expect(api.fetchControl).toHaveBeenCalledTimes(1));
  });

  it("summarises the active strategy read-only and links to edit it", async () => {
    render(<ControlPage />);
    await waitFor(() => expect(screen.getByText("default")).toBeTruthy());
    // The rows are shown, but not as inputs - editing lives on /strategy/.
    expect(screen.queryByLabelText("Damage threshold")).toBeNull();
    expect(screen.getByRole("link", { name: /edit strategy/i }).getAttribute("href"))
      .toBe("/strategy/");
  });
});
