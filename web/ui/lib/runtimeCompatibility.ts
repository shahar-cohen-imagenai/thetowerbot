import type { RuntimeMetadata } from "./types";

export interface CompatibilityResult {
  compatible: boolean;
  reasons: string[];
}

const MODES = new Set(["stopped", "paused", "observing", "automation_enabled"]);

function nullableString(value: unknown): boolean {
  return value === null || typeof value === "string";
}

export function isRuntimeMetadata(value: unknown): value is RuntimeMetadata {
  if (!value || typeof value !== "object") return false;
  const runtime = value as Partial<RuntimeMetadata>;
  return typeof runtime.api_version === "number"
    && !!runtime.backend && typeof runtime.backend === "object"
    && nullableString(runtime.backend.revision)
    && nullableString(runtime.backend.source_hash)
    && Number.isFinite(runtime.backend.started_at)
    && !!runtime.frontend && typeof runtime.frontend === "object"
    && nullableString(runtime.frontend.source_hash)
    && nullableString(runtime.frontend.expected_backend_hash)
    && (runtime.frontend.built_at === null || Number.isFinite(runtime.frontend.built_at))
    && Array.isArray(runtime.capabilities) && runtime.capabilities.every((item) => typeof item === "string")
    && nullableString(runtime.profile)
    && !!runtime.device && typeof runtime.device === "object"
    && nullableString(runtime.device.serial) && nullableString(runtime.device.game_version)
    && !!runtime.readiness && typeof runtime.readiness === "object"
    && MODES.has(runtime.readiness.mode as string)
    && Array.isArray(runtime.readiness.reasons) && runtime.readiness.reasons.every((item) => typeof item === "string");
}

function nonEmpty(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

export function checkRuntimeCompatibility(
  runtime: unknown,
  embeddedBackendHash = process.env.NEXT_PUBLIC_BACKEND_HASH,
  embeddedUiHash = process.env.NEXT_PUBLIC_UI_HASH,
  development = process.env.NEXT_PUBLIC_DEV_UI === "true",
): CompatibilityResult {
  if (runtime === undefined || runtime === null) {
    return { compatible: false, reasons: ["The backend did not provide runtime metadata."] };
  }
  if (!isRuntimeMetadata(runtime)) return { compatible: false, reasons: ["The backend provided malformed runtime metadata."] };

  const reasons: string[] = [];
  if (runtime.api_version !== 1) reasons.push(`Unsupported API version ${String(runtime.api_version)}; expected version 1.`);
  if (!nonEmpty(embeddedBackendHash) || !nonEmpty(runtime.backend?.source_hash)) {
    reasons.push("Backend build identity is missing.");
  } else if (runtime.backend.source_hash !== embeddedBackendHash) {
    reasons.push("The running backend build does not match this dashboard.");
  }
  if (!development && (!nonEmpty(embeddedUiHash) || !nonEmpty(runtime.frontend.source_hash))) {
    reasons.push("Dashboard build identity is missing.");
  } else if (!development && runtime.frontend.source_hash !== embeddedUiHash) {
    reasons.push("The served dashboard build does not match the loaded dashboard.");
  }
  if (!development && (!nonEmpty(runtime.frontend.expected_backend_hash) || !nonEmpty(runtime.backend.source_hash))) {
    reasons.push("The served dashboard's expected backend identity is missing.");
  } else if (!development && runtime.frontend.expected_backend_hash !== runtime.backend.source_hash) {
    reasons.push("The served dashboard expected backend does not match the running backend.");
  }
  return { compatible: reasons.length === 0, reasons };
}
