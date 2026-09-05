import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RuntimeGate } from "./RuntimeGate";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({ fetchStatus: vi.fn(), patchControl: vi.fn(), stopBot: vi.fn() }));

const validStatus = (capabilities = ["control", "lifecycle", "strategies", "autopilot", "advisor"]) => ({
  bot: { running: true, since: 1, error: null },
  runtime: {
    api_version: 1 as const,
    backend: { revision: "abc123", source_hash: "backend-hash", started_at: 10 },
    frontend: { source_hash: "ui-hash", expected_backend_hash: "backend-hash", built_at: 20 },
    capabilities,
    profile: "farm",
    device: { serial: "emulator-5554", game_version: null },
    readiness: { mode: "automation_enabled" as const, reasons: ["Purchases remain policy-gated"] },
  },
});

beforeEach(() => {
  vi.mocked(api.fetchStatus).mockReset().mockResolvedValue(validStatus() as never);
  vi.mocked(api.patchControl).mockReset().mockResolvedValue({} as never);
  vi.mocked(api.stopBot).mockReset().mockResolvedValue({ running: false, since: null, error: null });
  vi.stubEnv("NEXT_PUBLIC_BACKEND_HASH", "backend-hash");
  vi.stubEnv("NEXT_PUBLIC_UI_HASH", "ui-hash");
});

describe("RuntimeGate", () => {
  it("shows readiness without describing enabled automation as autonomous", async () => {
    render(<RuntimeGate><button>Ordinary action</button></RuntimeGate>);
    expect(await screen.findByText("Automation enabled")).toBeInTheDocument();
    expect(screen.queryByText(/fully autonomous/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ordinary action" })).toBeEnabled();
  });

  it("locks ordinary actions for a legacy API and offers recovery plus emergency controls", async () => {
    vi.mocked(api.fetchStatus).mockResolvedValue({ bot: { running: true } } as never);
    render(<RuntimeGate><button>Ordinary action</button></RuntimeGate>);
    expect(await screen.findByText(/controls locked/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ordinary action" })).toBeDisabled();
    expect(screen.getByText(/restart the backend/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /pause automation/i }));
    fireEvent.click(screen.getByRole("button", { name: /stop bot/i }));
    await waitFor(() => expect(api.patchControl).toHaveBeenCalledWith({ paused: true }));
    expect(api.stopBot).toHaveBeenCalledOnce();
  });

  it("fails closed when status cannot be loaded", async () => {
    vi.mocked(api.fetchStatus).mockRejectedValue(new Error("offline"));
    render(<RuntimeGate><button>Ordinary action</button></RuntimeGate>);
    expect(await screen.findByText(/status unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ordinary action" })).toBeDisabled();
  });

  it("survives malformed nested metadata with emergency controls available", async () => {
    vi.mocked(api.fetchStatus).mockResolvedValue({ runtime: { api_version: 1, readiness: { mode: "unknown", reasons: "bad" } } } as never);
    render(<RuntimeGate><button>Ordinary action</button></RuntimeGate>);
    expect(await screen.findByText(/controls locked/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /pause automation/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /stop bot/i })).toBeEnabled();
  });

  it("keeps emergency controls available while the first status request is pending", () => {
    vi.mocked(api.fetchStatus).mockReturnValue(new Promise(() => {}) as never);
    render(<RuntimeGate><button>Ordinary action</button></RuntimeGate>);
    expect(screen.getByRole("button", { name: /pause automation/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /stop bot/i })).toBeEnabled();
  });

  it("warns about unavailable features without locking capabilities that remain compatible", async () => {
    vi.mocked(api.fetchStatus).mockResolvedValue(validStatus(["control", "lifecycle"]) as never);
    render(<RuntimeGate><button>Ordinary action</button></RuntimeGate>);
    expect(await screen.findByText(/some features are unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ordinary action" })).toBeEnabled();
  });

  it("expands mobile-safe runtime details and calls out unknown game version", async () => {
    const { container } = render(<RuntimeGate><a href="/stats/">Stats</a></RuntimeGate>);
    await screen.findByText("Automation enabled");
    fireEvent.click(screen.getByText("Runtime details"));
    expect(screen.getByText("abc123")).toBeInTheDocument();
    expect(screen.getByText("farm")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.tagName === "DIV" && element.textContent === "Game version: Unknown")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Stats" })).toHaveAttribute("href", "/stats/");
    expect(container.querySelector("[data-runtime-banner]")).toHaveClass("w-full");
  });

  it("ignores an older overlapping poll after a newer response", async () => {
    vi.useFakeTimers();
    let resolveFirst!: (value: unknown) => void;
    vi.mocked(api.fetchStatus)
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }) as never)
      .mockResolvedValueOnce({ bot: { running: true } } as never);
    render(<RuntimeGate pollIntervalMs={10}><button>Ordinary action</button></RuntimeGate>);
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(screen.getByRole("button", { name: "Ordinary action" })).toBeDisabled();
    await act(async () => { resolveFirst(validStatus()); await Promise.resolve(); });
    expect(screen.getByRole("button", { name: "Ordinary action" })).toBeDisabled();
    vi.useRealTimers();
  });

  it("labels explicit development mode in runtime details", async () => {
    vi.stubEnv("NEXT_PUBLIC_DEV_UI", "true");
    vi.mocked(api.fetchStatus).mockResolvedValue(validStatus() as never);
    render(<RuntimeGate><button>Action</button></RuntimeGate>);
    expect(await screen.findByText(/development UI/i)).toBeInTheDocument();
  });
});
