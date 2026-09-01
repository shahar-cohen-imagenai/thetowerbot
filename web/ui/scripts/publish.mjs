// Copies the exported site into web/static/, which is tracked in git so a
// fresh clone can run `uv run tower_bot.py --web` with no node installed.
import { cp, rm, writeFile, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { uiHash } from "./manifest.mjs";

const uiRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const outDir = path.join(uiRoot, "out");
const staticDir = path.join(uiRoot, "..", "static");

try {
  await access(outDir);
} catch {
  console.error("no out/ - run `next build` first");
  process.exit(1);
}

// Replace rather than merge: a file deleted from the app must disappear from
// the published site, and a merge would leave it there forever.
await rm(staticDir, { recursive: true, force: true });
await cp(outDir, staticDir, { recursive: true });

await writeFile(
  path.join(staticDir, ".build-manifest.json"),
  JSON.stringify({ hash: await uiHash(uiRoot), built_at: Date.now() / 1000 }, null, 2) + "\n",
);
console.log(`published -> ${staticDir}`);
