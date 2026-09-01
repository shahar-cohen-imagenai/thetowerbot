// The hash the freshness check compares. Content, never mtimes: git does not
// preserve modification times, so on a fresh clone every file carries its
// checkout time and an mtime comparison decides nothing.
//
// Must stay byte-for-byte equivalent to tools/ui_manifest.py. Both walk the
// same roots, sort by POSIX relative path, and feed "path\n" then the file
// bytes into one SHA-256.
import { createHash } from "node:crypto";
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";

// Everything that changes what the built site contains.
export const SOURCE_ROOTS = ["app", "components", "lib", "public"];
export const SOURCE_FILES = [
  "package.json",
  "package-lock.json",
  "next.config.ts",
  "tsconfig.json",
];

async function walk(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return []; // an optional root (public/) may not exist
  }
  const found = [];
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    const full = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) {
      // A symlink's Dirent is neither isFile() nor isDirectory(), so it would
      // otherwise vanish silently. Resolve it with stat(): follow it into the
      // hash when it points at a regular file (the bundler reads through the
      // link, so an edit to the target changes the build and must be caught
      // here), but do not descend into it when it points at a directory
      // (bounds the walk and avoids a symlink cycle). Matches Python's
      // rglob()+is_file(), which follows file symlinks but never descends
      // into symlinked directories.
      let resolved;
      try {
        resolved = await stat(full);
      } catch {
        continue; // broken symlink: nothing to hash
      }
      if (resolved.isFile()) found.push(full);
    } else if (entry.isDirectory()) {
      found.push(...(await walk(full)));
    } else if (entry.isFile()) {
      found.push(full);
    }
  }
  return found;
}

export async function uiHash(uiRoot) {
  const files = [];
  for (const root of SOURCE_ROOTS) files.push(...(await walk(path.join(uiRoot, root))));
  for (const name of SOURCE_FILES) {
    const full = path.join(uiRoot, name);
    try {
      if ((await stat(full)).isFile()) files.push(full);
    } catch {
      // a missing optional file simply contributes nothing
    }
  }
  const relative = files
    .map((full) => path.relative(uiRoot, full).split(path.sep).join("/"))
    .sort();

  const digest = createHash("sha256");
  for (const rel of relative) {
    digest.update(rel + "\n");
    digest.update(await readFile(path.join(uiRoot, rel)));
  }
  return digest.digest("hex");
}
