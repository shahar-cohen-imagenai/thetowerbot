import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import StrategyPage from "./page";

const strategy = {
  name: "default",
  actions: [
    { name: "Damage", template: "d.png", enabled: true, threshold: 0.9, brightness_ratio: 0.75 },
  ],
  affordability: "digits",
  interval: 2,
  click_cooldown: 1,
  auto_navigate: false,
  max_runs: null,
  navigation_cooldown: 3,
  screen_confirmations: 2,
};

const api = vi.hoisted(() => ({
  fetchStrategies: vi.fn(),
  fetchStrategy: vi.fn(),
  saveStrategy: vi.fn(),
  activateStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
  fetchControl: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/lib/useControlSync", () => ({ useControlSync: () => {} }));

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchStrategies.mockResolvedValue({ active: "default", names: ["default", "crit"] });
  api.fetchStrategy.mockResolvedValue(strategy);
  api.fetchControl.mockResolvedValue({
    paused: false, strategy, affordability_available: ["digits", "brightness"],
  });
  api.saveStrategy.mockImplementation((_n: string, body: unknown) => Promise.resolve(body));
});

describe("StrategyPage", () => {
  it("loads the active profile on arrival", async () => {
    render(<StrategyPage />);
    await waitFor(() => expect(screen.getByTestId("action-row")).toBeTruthy());
    expect(api.fetchStrategy).toHaveBeenCalledWith("default");
  });

  it("saving sends the whole profile under the selected name", async () => {
    render(<StrategyPage />);
    await waitFor(() => screen.getByLabelText("Scan interval (s)"));
    fireEvent.blur(screen.getByLabelText("Scan interval (s)"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() =>
      expect(api.saveStrategy).toHaveBeenCalledWith(
        "default",
        expect.objectContaining({ interval: 5 }),
      ),
    );
  });

  it("Save is disabled until something changes", async () => {
    render(<StrategyPage />);
    await waitFor(() => screen.getByText("Save"));
    expect(screen.getByText("Save").hasAttribute("disabled")).toBe(true);
  });

  it("Revert discards local edits without calling the server", async () => {
    render(<StrategyPage />);
    await waitFor(() => screen.getByLabelText("Scan interval (s)"));
    fireEvent.blur(screen.getByLabelText("Scan interval (s)"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByText("Revert"));
    await waitFor(() =>
      expect(screen.getByLabelText("Scan interval (s)").getAttribute("value")).toBe("2"),
    );
    expect(api.saveStrategy).not.toHaveBeenCalled();
  });

  it("shows the server's reason when a save is rejected", async () => {
    api.saveStrategy.mockRejectedValue(new Error("interval: must be between 0.1 and 3600"));
    render(<StrategyPage />);
    await waitFor(() => screen.getByLabelText("Scan interval (s)"));
    fireEvent.blur(screen.getByLabelText("Scan interval (s)"), {
      target: { value: "9999" },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(screen.getByText(/must be between/)).toBeTruthy());
  });

  it("switching profiles fetches the other one", async () => {
    render(<StrategyPage />);
    await waitFor(() => screen.getByLabelText("Strategy"));
    fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "crit" } });
    await waitFor(() => expect(api.fetchStrategy).toHaveBeenCalledWith("crit"));
  });
});
