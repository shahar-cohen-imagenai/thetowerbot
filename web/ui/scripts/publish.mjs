// Copies the exported site into ignored web/static/, which FastAPI serves.
import { cp, rm, writeFile, access, rename } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runtimeHashes } from "./runtime-identity.mjs";

const uiRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const outDir = path.join(uiRoot, "out");
const staticDir = path.join(uiRoot, "..", "static");
const stagingDir = path.join(uiRoot, "..", `.static-publish-${process.pid}`);
const [expectedFrontend, expectedBackend] = process.argv.slice(2);

if (!expectedFrontend || !expectedBackend) {
  console.error("publish requires the pre-build frontend and backend hashes");
  process.exit(1);
}

const current = await runtimeHashes();
if (current.frontend !== expectedFrontend || current.backend !== expectedBackend) {
  console.error("source changed before publish; existing web/static was left untouched");
  process.exit(1);
}

try {
  await access(outDir);
} catch {
  console.error("no out/ - run `next build` first");
  process.exit(1);
}

await rm(stagingDir, { recursive: true, force: true });
await cp(outDir, stagingDir, { recursive: true });

const afterCopy = await runtimeHashes();
if (afterCopy.frontend !== expectedFrontend || afterCopy.backend !== expectedBackend) {
  await rm(stagingDir, { recursive: true, force: true });
  console.error("source changed during publish; existing web/static was left untouched");
  process.exit(1);
}

await writeFile(
  path.join(stagingDir, ".build-manifest.json"),
  JSON.stringify({
    hash: expectedFrontend,
    backend_hash: expectedBackend,
    built_at: Date.now() / 1000,
  }, null, 2) + "\n",
);
// Replace rather than merge: deleted app files must disappear from the site.
await rm(staticDir, { recursive: true, force: true });
await rename(stagingDir, staticDir);
console.log(`published -> ${staticDir}`);
