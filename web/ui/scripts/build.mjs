import { rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { runtimeHashes, uiRoot } from "./runtime-identity.mjs";

const before = await runtimeHashes();
const next = path.join(uiRoot, "node_modules", ".bin", "next");
const result = spawnSync(next, ["build"], {
  cwd: uiRoot,
  stdio: "inherit",
  env: {
    ...process.env,
    NEXT_PUBLIC_UI_HASH: before.frontend,
    NEXT_PUBLIC_BACKEND_HASH: before.backend,
    NEXT_PUBLIC_DEV_UI: "false",
  },
});
if (result.status !== 0) process.exit(result.status ?? 1);

const after = await runtimeHashes();
if (after.frontend !== before.frontend || after.backend !== before.backend) {
  await rm(path.join(uiRoot, "out"), { recursive: true, force: true });
  console.error("source changed during build; discarded out/ and did not publish");
  process.exit(1);
}

const publish = spawnSync(
  process.execPath,
  [path.join(uiRoot, "scripts", "publish.mjs"), before.frontend, before.backend],
  { cwd: uiRoot, stdio: "inherit" },
);
process.exit(publish.status ?? 1);
