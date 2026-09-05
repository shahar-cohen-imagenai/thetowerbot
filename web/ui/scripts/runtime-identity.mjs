import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { uiHash } from "./manifest.mjs";

export const uiRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
export const projectRoot = path.resolve(uiRoot, "..", "..");

export async function runtimeHashes() {
  const frontend = await uiHash(uiRoot);
  const backend = execFileSync(
    "python3",
    ["-m", "runtime_identity", "--root", projectRoot],
    { cwd: projectRoot, encoding: "utf8" },
  ).trim();
  if (!frontend || !backend) throw new Error("could not compute runtime build identities");
  return { frontend, backend };
}
