import { spawnSync } from "node:child_process";
import path from "node:path";
import { runtimeHashes, uiRoot } from "./runtime-identity.mjs";

const hashes = await runtimeHashes();
const next = path.join(uiRoot, "node_modules", ".bin", "next");
const result = spawnSync(next, ["dev", ...process.argv.slice(2)], {
  cwd: uiRoot,
  stdio: "inherit",
  env: {
    ...process.env,
    NEXT_PUBLIC_UI_HASH: hashes.frontend,
    NEXT_PUBLIC_BACKEND_HASH: hashes.backend,
    NEXT_PUBLIC_DEV_UI: "true",
  },
});
process.exit(result.status ?? 1);
