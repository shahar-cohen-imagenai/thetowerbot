import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError, activateStrategy, deleteStrategy, fetchControl, fetchErrors,
  fetchRunEvents, fetchRuns, fetchStats, fetchStatus, fetchStrategies,
  fetchStrategy, fetchUnknown, patchControl, saveStrategy, shutdown,
  startBot, stopBot,
} from "./api";
import type { Strategy } from "./types";

/** Every page test mocks `@/lib/api` wholesale and the Python suite tests the
 * routes from the server side, so nothing else in either suite ever asserts
 * that a fetcher points at the URL its server actually serves. A typo in one
 * of these paths would ship green on both. */

const fetchMock = vi.fn();

function respond(status: number, body: unknown): void {
  fetchMock.mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

/** The (url, init) the single fetch call was made with. */
function callArgs(): [string, RequestInit | undefined] {
  expect(fetchMock).toHaveBeenCalledTimes(1);
  return fetchMock.mock.calls[0] as [string, RequestInit | undefined];
}

const strategy: Strategy = {
  name: "default",
  actions: [],
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

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  respond(200, {});
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("read routes", () => {
  const reads: [string, () => Promise<unknown>, string][] = [
    ["fetchStatus", fetchStatus, "/api/status"],
    ["fetchRuns", () => fetchRuns(), "/api/runs?limit=30"],
    ["fetchRuns(5)", () => fetchRuns(5), "/api/runs?limit=5"],
    ["fetchRunEvents", () => fetchRunEvents(7), "/api/runs/7/events"],
    ["fetchUnknown", fetchUnknown, "/api/unknown"],
    ["fetchStats", fetchStats, "/api/stats"],
    ["fetchErrors", () => fetchErrors(), "/api/errors?limit=100"],
    ["fetchControl", fetchControl, "/api/control"],
    ["fetchStrategies", fetchStrategies, "/api/strategies"],
    ["fetchStrategy", () => fetchStrategy("crit"), "/api/strategies/crit"],
  ];

  it.each(reads)("%s GETs %s", async (_name, call, url) => {
    await call();
    const [got, init] = callArgs();
    expect(got).toBe(url);
    // No `method` at all is a GET; anything else here would be a bug.
    expect(init?.method).toBeUndefined();
  });

  it("percent-encodes a profile name into the path", async () => {
    // The server refuses such a name, but it must arrive as a path segment
    // rather than as an extra one.
    await fetchStrategy("a/b").catch(() => {});
    expect(callArgs()[0]).toBe("/api/strategies/a%2Fb");
  });
});

describe("write routes", () => {
  it("saveStrategy PUTs the whole profile under its name", async () => {
    respond(200, strategy);
    await saveStrategy("crit", strategy);
    const [url, init] = callArgs();
    expect(url).toBe("/api/strategies/crit");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual(strategy);
  });

  it("activateStrategy POSTs to the profile's activate sub-route", async () => {
    respond(200, { active: "crit", names: ["crit"] });
    await activateStrategy("crit");
    const [url, init] = callArgs();
    expect(url).toBe("/api/strategies/crit/activate");
    expect(init?.method).toBe("POST");
  });

  it("deleteStrategy DELETEs the profile", async () => {
    respond(200, { active: "default", names: ["default"] });
    await deleteStrategy("crit");
    const [url, init] = callArgs();
    expect(url).toBe("/api/strategies/crit");
    expect(init?.method).toBe("DELETE");
  });

  it("patchControl PATCHes /api/control with the patch", async () => {
    respond(200, { paused: true, strategy, affordability_available: [] });
    await patchControl({ paused: true });
    const [url, init] = callArgs();
    expect(url).toBe("/api/control");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ paused: true });
  });

  const lifecycle: [string, () => Promise<unknown>, string][] = [
    ["startBot", startBot, "/api/bot/start"],
    ["stopBot", stopBot, "/api/bot/stop"],
    ["shutdown", shutdown, "/api/shutdown"],
  ];

  it.each(lifecycle)("%s POSTs %s", async (_name, call, url) => {
    await call();
    const [got, init] = callArgs();
    expect(got).toBe(url);
    expect(init?.method).toBe("POST");
  });
});

describe("failures", () => {
  it("a failed read carries the server's reason and its status", async () => {
    respond(422, { detail: "crit.json is not valid JSON" });
    const err = await fetchStrategy("crit").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).message).toBe("crit.json is not valid JSON");
  });

  it("a failed write flattens pydantic's structured detail into one line", async () => {
    respond(422, { detail: [{ msg: "interval: too large" }, { msg: "name: bad" }] });
    const err = await saveStrategy("crit", strategy).catch((e: unknown) => e);
    expect((err as ApiError).message).toBe("interval: too large; name: bad");
  });

  it("falls back to method, path and status when there is no detail", async () => {
    respond(500, {});
    const err = await startBot().catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).message).toBe("POST /api/bot/start -> 500");
  });

  it("keeps 409 distinguishable from any other failure", async () => {
    respond(409, { detail: "the bot is already running" });
    const err = await startBot().catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(409);
  });
});
