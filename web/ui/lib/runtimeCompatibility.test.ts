import { describe, expect, it } from "vitest";
import type { RuntimeMetadata } from "./types";
import { checkRuntimeCompatibility } from "./runtimeCompatibility";

const runtime = (overrides: Partial<RuntimeMetadata> = {}): RuntimeMetadata => ({
  api_version: 1,
  backend: { revision: "abc", source_hash: "backend-hash", started_at: 10 },
  frontend: { source_hash: "ui-hash", expected_backend_hash: "backend-hash", built_at: 20 },
  capabilities: ["control", "lifecycle"],
  profile: "default",
  device: { serial: "emulator-5554", game_version: null },
  readiness: { mode: "observing", reasons: [] },
  ...overrides,
});

describe("checkRuntimeCompatibility", () => {
  it("accepts matching versioned runtime metadata", () => {
    expect(checkRuntimeCompatibility(runtime(), "backend-hash", "ui-hash")).toEqual({ compatible: true, reasons: [] });
  });

  it("fails closed when the old API omits runtime metadata", () => {
    expect(checkRuntimeCompatibility(undefined, "backend-hash", "ui-hash")).toMatchObject({
      compatible: false,
      reasons: [expect.stringMatching(/runtime metadata/i)],
    });
  });

  it("reports every identity mismatch", () => {
    const result = checkRuntimeCompatibility(runtime({
      api_version: 2 as 1,
      backend: { revision: "abc", source_hash: "old", started_at: 10 },
      frontend: { source_hash: "stale-ui", expected_backend_hash: "other", built_at: 20 },
    }), "backend-hash", "ui-hash");
    expect(result.compatible).toBe(false);
    expect(result.reasons.join(" ")).toMatch(/API version/i);
    expect(result.reasons.join(" ")).toMatch(/backend build/i);
    expect(result.reasons.join(" ")).toMatch(/dashboard build/i);
    expect(result.reasons.join(" ")).toMatch(/expected backend/i);
  });

  it("treats null or malformed identity fields as incompatible", () => {
    expect(checkRuntimeCompatibility(runtime({
      backend: { revision: null, source_hash: null, started_at: 10 },
    }), "backend-hash", "ui-hash").compatible).toBe(false);
    expect(checkRuntimeCompatibility(runtime({
      backend: { revision: "abc", source_hash: "backend-hash", started_at: Number.NaN },
    }), "backend-hash", "ui-hash").compatible).toBe(false);
  });

  it("allows the development server to omit served frontend identity only", () => {
    const result = checkRuntimeCompatibility(runtime({
      frontend: { source_hash: null, expected_backend_hash: null, built_at: null },
    }), "backend-hash", "ui-hash", true);
    expect(result).toEqual({ compatible: true, reasons: [] });
    expect(checkRuntimeCompatibility(runtime({ api_version: 2 as 1 }), "backend-hash", "ui-hash", true).compatible).toBe(false);
  });
});
