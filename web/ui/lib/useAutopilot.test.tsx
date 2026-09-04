import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAutopilot } from "./useAutopilot";
import { fetchAutopilot } from "./api";
vi.mock("./api", () => ({ fetchAutopilot: vi.fn() }));
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});
describe("useAutopilot", () => {
  it("stops polling after unmount and does not overlap pending reads", async () => {
    vi.useFakeTimers();
    let resolve!: (value: null) => void;
    vi.mocked(fetchAutopilot).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done as unknown as typeof resolve;
        }),
    );
    const hook = renderHook(() => useAutopilot());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fetchAutopilot).toHaveBeenCalledTimes(1);
    hook.unmount();
    await act(async () => {
      resolve(null);
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fetchAutopilot).toHaveBeenCalledTimes(1);
  });
});
