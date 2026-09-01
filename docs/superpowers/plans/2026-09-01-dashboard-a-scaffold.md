# Dashboard Rewrite, Plan A — Next.js Scaffold and Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-written `web/static/index.html` with a Next.js/TypeScript application that FastAPI serves from a committed static build, reaching feature parity with today's dashboard.

**Architecture:** A Next.js App Router project lives in `web/ui/`. `next build` runs in static-export mode and a small node script publishes the result into `web/static/`, which is tracked in git. FastAPI stops hand-serving one HTML file and mounts `web/static/` instead, after its `/api` routes so route precedence keeps the API reachable. In development, `output` is left unset so `next dev` runs a real dev server with a rewrite proxying `/api` to the bot on `:8765`.

**Tech Stack:** Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS v4, shadcn/ui, Vitest + Testing Library. Python side unchanged except `web/app.py`. Node 20.20.2 and npm 10.8.2 are already installed.

**Spec:** `docs/superpowers/specs/2026-09-01-tower-bot-dashboard-ui-design.md`

## Global Constraints

- Python >= 3.12. Type hints on every function. `from __future__ import annotations` at the top of every Python module.
- Python dependencies are managed with `uv add` / `uv add --dev` — **never hand-edit `pyproject.toml`**.
- Node dependencies are managed with `npm install` inside `web/ui/` — never hand-edit `web/ui/package.json`.
- Every Python test runs under `uv run pytest`. Every UI test runs under `npm test` inside `web/ui/`.
- `web/static/` is **tracked in git**. `web/ui/node_modules/`, `web/ui/.next/` and `web/ui/out/` are **not**.
- The dashboard binds loopback only. Do not add authentication, and do not add any route that writes to the database.
- Commit after every task. Never commit a red test suite.

## Deviation from the spec, decided during planning

The spec (§8) specifies the build-freshness check as "fails if `web/static/` is older than `web/ui/`". **Implement it as a content hash instead of mtimes.** Git does not preserve modification times, so on a fresh clone every file carries its checkout time in arbitrary order and an mtime comparison is a coin flip. Task 3 writes a `.build-manifest.json` into `web/static/` holding a SHA-256 over the UI sources, and the pytest recomputes that hash from the working tree and compares. Same guarantee, deterministic on any clone.

## File Structure

| Path | Responsibility |
|---|---|
| `web/ui/package.json` | scripts and dependencies (npm-managed) |
| `web/ui/next.config.ts` | static export in prod, `/api` rewrite in dev, `trailingSlash` |
| `web/ui/scripts/publish.mjs` | copy `out/` → `web/static/`, write the build manifest |
| `web/ui/scripts/manifest.mjs` | the shared hash function, imported by publish |
| `web/ui/lib/types.ts` | TypeScript mirrors of every Python payload |
| `web/ui/lib/api.ts` | one typed fetch wrapper per endpoint |
| `web/ui/lib/format.ts` | pure formatting: clock, money, event → line |
| `web/ui/lib/useEventStream.ts` | the single SSE subscription + its reducer |
| `web/ui/app/layout.tsx` | sidebar shell, theme provider |
| `web/ui/app/page.tsx` | the Live page |
| `web/ui/components/*.tsx` | stat cards, event feed, run table, snapshot strip |
| `web/static/` | published build output (tracked) |
| `web/app.py` | modify: drop the `/` route, mount `web/static/` |
| `tools/ui_manifest.py` | Python side of the hash, used by the freshness test |
| `tests/test_web_build.py` | build freshness + static serving tests |

---

### Task 1: Scaffold the Next.js project

**Files:**
- Create: `web/ui/` (via `create-next-app`)
- Create: `web/ui/next.config.ts`
- Modify: `.gitignore`

**Interfaces:**
- Produces: a `web/ui` npm project whose `npm run build` emits a static site into `web/ui/out/`.

- [ ] **Step 1: Scaffold**

Run from the repo root. The flags answer every prompt, so this is non-interactive:

```bash
npx --yes create-next-app@15 web/ui \
  --typescript --tailwind --eslint --app \
  --src-dir=false --import-alias "@/*" --use-npm --no-turbopack
```

- [ ] **Step 2: Replace the generated config**

Overwrite `web/ui/next.config.ts` with exactly this:

```ts
import type { NextConfig } from "next";

// `output: "export"` is production-only on purpose. Static export does not
// support rewrites, and the dev proxy below is how `next dev` on :3000 reaches
// the bot's API on :8765. Leaving `output` unset in development gives us a real
// dev server (HMR, rewrites); setting it for the build gives us plain files
// FastAPI can serve with no node at runtime.
const isProd = process.env.NODE_ENV === "production";

const nextConfig: NextConfig = {
  output: isProd ? "export" : undefined,
  // Emits `out/runs/index.html` rather than `out/runs.html`, which is the
  // layout Starlette's StaticFiles(html=True) resolves for the URL `/runs/`.
  trailingSlash: true,
  // No node at runtime means no image optimisation server.
  images: { unoptimized: true },
  async rewrites() {
    if (isProd) return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8765/api/:path*",
      },
    ];
  },
};

export default nextConfig;
```

- [ ] **Step 3: Ignore the node artefacts**

Append to the repo-root `.gitignore`:

```
# Next.js UI build artefacts. web/static/ is deliberately NOT ignored - it is
# the committed build the bot serves with no node installed.
web/ui/node_modules/
web/ui/.next/
web/ui/out/
web/ui/.eslintcache
```

- [ ] **Step 4: Verify the build produces a static site**

```bash
cd web/ui && npm run build && ls out/index.html out/_next
```
Expected: both paths exist.

- [ ] **Step 5: Commit**

```bash
git add .gitignore web/ui
git commit -m "feat: scaffold the Next.js dashboard project"
```

---

### Task 2: Publish the build into web/static

**Files:**
- Create: `web/ui/scripts/manifest.mjs`
- Create: `web/ui/scripts/publish.mjs`
- Modify: `web/ui/package.json` (scripts only, via npm — see step 3)

**Interfaces:**
- Consumes: `web/ui/out/` from Task 1.
- Produces: `npm run build` in `web/ui` leaves a complete site in `web/static/`, including `web/static/.build-manifest.json` with shape `{ "hash": "<64 hex chars>", "built_at": <unix seconds> }`.

- [ ] **Step 1: Write the hash function**

Create `web/ui/scripts/manifest.mjs`:

```js
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
    if (entry.isDirectory()) found.push(...(await walk(full)));
    else if (entry.isFile()) found.push(full);
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
```

- [ ] **Step 2: Write the publish script**

Create `web/ui/scripts/publish.mjs`:

```js
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
```

- [ ] **Step 3: Point `npm run build` at it**

Edit the `scripts` block of `web/ui/package.json` so `build` publishes too. This is the one hand-edit to that file the constraints allow, because it is a script and not a dependency:

```json
"scripts": {
  "dev": "next dev",
  "build": "next build && node scripts/publish.mjs",
  "start": "next start",
  "lint": "eslint"
}
```

- [ ] **Step 4: Build and verify**

```bash
cd web/ui && npm run build
ls ../static/index.html ../static/.build-manifest.json
```
Expected: both exist.

- [ ] **Step 5: Commit**

```bash
git add web/ui/scripts web/ui/package.json web/static
git commit -m "feat: publish the built dashboard into web/static"
```

---

### Task 3: Serve the built app from FastAPI

**Files:**
- Modify: `web/app.py` (the `dashboard()` route, and the end of `create_app`)
- Test: `tests/test_web_build.py`

**Interfaces:**
- Consumes: `web/static/` from Task 2.
- Produces: `create_app(...)` serves the built site at `/` and keeps every `/api/*` route working.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_build.py`:

```python
"""The built dashboard is served, and the API still wins on /api."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from events import EventBus
from sinks.sse import SseSink
from sinks.state import BotState
from web.app import create_app

STATIC_DIR = Path(__file__).parent.parent / "web" / "static"


@pytest.fixture
def client() -> TestClient:
    bus = EventBus()
    return TestClient(
        create_app(
            state=BotState(),
            sse=SseSink(),
            bus=bus,
            db_path=None,
            unknown_dir=config.UNKNOWN_DIR,
            stop=threading.Event(),
        )
    )


def test_root_serves_the_built_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    # Next injects this on every exported page; the old hand-written file
    # never contained it, so this fails loudly if the build is not being served.
    assert "__NEXT_DATA__" in response.text or "/_next/" in response.text


def test_next_assets_are_served(client: TestClient) -> None:
    assets = list((STATIC_DIR / "_next").rglob("*.js"))
    assert assets, "no built JS - run `npm run build` in web/ui"
    url = "/_next/" + str(assets[0].relative_to(STATIC_DIR / "_next")).replace("\\", "/")
    assert client.get(url).status_code == 200


def test_api_routes_still_win_over_the_static_mount(client: TestClient) -> None:
    # The mount is registered at "/", so this is the regression that matters:
    # if it were registered before the API routes it would swallow them.
    response = client.get("/api/status")
    assert response.status_code == 200
    assert "screen" in response.json()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_web_build.py -v`
Expected: `test_root_serves_the_built_index` FAILS — the old route returns the hand-written HTML, which contains neither marker.

- [ ] **Step 3: Replace the route with a mount**

In `web/app.py`, delete the `dashboard()` route entirely:

```python
    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        # Read per request rather than cached at import: editing the page and
        # hitting refresh is the whole development loop for it.
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")
```

and add this immediately before `return app` at the end of `create_app`:

```python
    # Last, deliberately. Starlette matches routes in registration order and a
    # mount at "/" matches everything, so every /api route above must already
    # be registered or the mount would swallow the whole API.
    #
    # html=True resolves "/runs/" to "runs/index.html", which is the layout
    # next.config.ts's trailingSlash:true produces.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    else:
        @app.get("/", response_class=HTMLResponse)
        def not_built() -> str:
            # Only reachable in a working tree whose build was deleted; the
            # committed web/static/ means a fresh clone never sees this.
            return (
                "<h1>Dashboard not built</h1>"
                "<p>Run <code>npm run build</code> in <code>web/ui</code>.</p>"
            )

    return app
```

Update the imports at the top of `web/app.py`:

```python
from fastapi.staticfiles import StaticFiles
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web_build.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green. `tests/test_web_api.py` exercises the API routes and must be unaffected — if anything there now 404s, the mount was registered too early.

- [ ] **Step 6: Commit**

```bash
git add web/app.py tests/test_web_build.py
git commit -m "feat: serve the built dashboard instead of one static file"
```

---

### Task 4: The build-freshness check

**Files:**
- Create: `tools/ui_manifest.py`
- Modify: `tests/test_web_build.py` (append)

**Interfaces:**
- Consumes: `web/static/.build-manifest.json` from Task 2.
- Produces: `tools.ui_manifest.ui_hash(ui_root: Path) -> str`, byte-identical to `scripts/manifest.mjs`.

- [ ] **Step 1: Write the failing test**

Add `import json` and `from tools.ui_manifest import ui_hash` to the import block at the **top** of `tests/test_web_build.py`, then append the constant and the test to the end:

```python
UI_DIR = Path(__file__).parent.parent / "web" / "ui"


def test_committed_build_matches_the_ui_sources() -> None:
    """The committed build is not stale.

    Committing build output buys `git clone && --web` with no node, and costs
    exactly one new failure mode: source edited, build not rerun, stale bundle
    shipped. This is that failure mode's test.
    """
    manifest_path = STATIC_DIR / ".build-manifest.json"
    assert manifest_path.is_file(), "no build manifest - run `npm run build` in web/ui"
    recorded = json.loads(manifest_path.read_text(encoding="utf-8"))["hash"]
    assert recorded == ui_hash(UI_DIR), (
        "web/static/ is stale against web/ui/ - run `npm run build` in web/ui "
        "and commit the result"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_web_build.py::test_committed_build_matches_the_ui_sources -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.ui_manifest'`.

- [ ] **Step 3: Write the Python hash**

Create `tools/ui_manifest.py`:

```python
"""The Python half of the UI build manifest.

Must stay byte-for-byte equivalent to web/ui/scripts/manifest.mjs: same roots,
same sort, same feed order. If you change one, change both, and the test in
tests/test_web_build.py will tell you the moment they diverge.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_ROOTS = ("app", "components", "lib", "public")
SOURCE_FILES = (
    "package.json",
    "package-lock.json",
    "next.config.ts",
    "tsconfig.json",
)


def ui_hash(ui_root: Path) -> str:
    """SHA-256 over every source file that changes what the build contains."""
    paths: list[Path] = []
    for root in SOURCE_ROOTS:
        directory = ui_root / root
        if directory.is_dir():
            paths.extend(p for p in directory.rglob("*") if p.is_file())
    for name in SOURCE_FILES:
        candidate = ui_root / name
        if candidate.is_file():
            paths.append(candidate)

    digest = hashlib.sha256()
    for relative in sorted(p.relative_to(ui_root).as_posix() for p in paths):
        digest.update((relative + "\n").encode("utf-8"))
        digest.update((ui_root / relative).read_bytes())
    return digest.hexdigest()
```

Create `tools/__init__.py` if it does not exist:

```bash
test -f tools/__init__.py || touch tools/__init__.py
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_web_build.py -v`
Expected: 4 passed. If the hash mismatches, the two implementations have diverged — compare the sorted path lists before touching anything else.

- [ ] **Step 5: Commit**

```bash
git add tools/ui_manifest.py tools/__init__.py tests/test_web_build.py
git commit -m "test: fail when the committed dashboard build is stale"
```

---

### Task 5: Types, the API client, and the test runner

**Files:**
- Create: `web/ui/lib/types.ts`, `web/ui/lib/api.ts`, `web/ui/lib/format.ts`
- Create: `web/ui/vitest.config.ts`, `web/ui/lib/format.test.ts`

**Interfaces:**
- Produces: `BotEvent`, `StatusPayload`, `RunRow`, `Snapshot` types; `fetchStatus()`, `fetchRuns(limit)`, `fetchRunEvents(id)`, `fetchUnknown()`; `describe(event)`, `clock(ts)`, `money(n)`.

- [ ] **Step 1: Install the test toolchain**

```bash
cd web/ui && npm install --save-dev vitest @vitejs/plugin-react jsdom \
  @testing-library/react @testing-library/dom @testing-library/jest-dom
```

Add to the `scripts` block of `web/ui/package.json`:

```json
"test": "vitest run"
```

- [ ] **Step 2: Configure vitest**

Create `web/ui/vitest.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
  test: { environment: "jsdom", globals: true },
});
```

- [ ] **Step 3: Write the failing test**

Create `web/ui/lib/format.test.ts`:

```ts
import { describe as group, expect, it } from "vitest";
import { clock, describe, money } from "./format";

group("money", () => {
  it("renders a dash for absent values", () => {
    expect(money(null)).toBe("-");
    expect(money(undefined)).toBe("-");
  });
  it("prefixes a dollar sign", () => {
    expect(money(1200)).toBe("$1200");
  });
});

group("clock", () => {
  it("renders unix seconds as a wall clock", () => {
    // 1970-01-01T00:00:05Z, formatted in whatever zone the test runs in, so
    // assert the shape rather than the digits.
    expect(clock(5)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});

group("describe", () => {
  it("renders a tap with its score and price", () => {
    const line = describe({
      type: "Tapped", seq: 1, ts: 0, action: "Damage",
      x: 10, y: 20, score: 0.98123, price: 500, wallet: 900,
    });
    expect(line).toContain("TAP");
    expect(line).toContain("Damage");
    expect(line).toContain("0.981");
    expect(line).toContain("$500");
  });

  it("falls back to the bare type for an event it has never seen", () => {
    expect(describe({ type: "SomethingNew", seq: 2, ts: 0 } as never)).toBe("SomethingNew");
  });

  it("renders a screen change using `curr`", () => {
    const line = describe({
      type: "ScreenChanged", seq: 3, ts: 0,
      prev: "MENU", curr: "IN_RUN", confidence: 0.999, scores: {},
    });
    expect(line).toContain("MENU");
    expect(line).toContain("IN_RUN");
  });
});
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd web/ui && npm test`
Expected: FAIL — `Failed to resolve import "./format"`.

- [ ] **Step 5: Write the types**

Create `web/ui/lib/types.ts`:

```ts
// Mirrors of the Python payloads. Every event field here comes from the
// dataclasses in events.py: sinks/sse.py's to_payload() is dataclasses.asdict()
// plus a `type` discriminator, so these are the wire shapes exactly.

export interface EventBase {
  seq: number;
  ts: number;
}

export type BotEvent =
  | (EventBase & { type: "ScreenChanged"; prev: string; curr: string; confidence: number; scores: Record<string, number> })
  | (EventBase & { type: "ScanCompleted"; screen: string; duration_ms: number; wallet: number | null })
  | (EventBase & { type: "Tapped"; action: string; x: number; y: number; score: number; price: number | null; wallet: number | null })
  | (EventBase & { type: "Skipped"; action: string; reason: string; detail: string })
  | (EventBase & { type: "RunStarted"; run_id: number })
  | (EventBase & { type: "RunEnded"; run_id: number; duration: number; wave: number | null; coins: number | null; tier: number | null; abandoned: boolean })
  | (EventBase & { type: "Navigated"; target: string })
  | (EventBase & { type: "UnknownScreen"; snapshot_path: string; best_anchor: string; best_score: number })
  | (EventBase & { type: "BotError"; message: string; traceback: string });

/** A row from the `events` table, which carries columns plus a JSON blob. */
export interface StoredEvent {
  seq: number;
  run_id: number | null;
  ts: number;
  type: string;
  screen: string | null;
  action: string | null;
  reason: string | null;
  score: number | null;
  price: number | null;
  wallet: number | null;
  detail: Record<string, unknown>;
}

export interface CurrentRun {
  id: number;
  started_at: number;
  elapsed: number;
  taps: Record<string, number>;
}

export interface StatusPayload {
  screen: string;
  uptime: number;
  scans: number;
  taps: Record<string, number>;
  skips: Record<string, number>;
  runs_completed: number;
  run: CurrentRun | null;
  wallet: number | null;
  last_error: string | null;
  tail: unknown[];
  dropped: number;
}

/** A row from the `runs` table. */
export interface RunRow {
  id: number;
  started_at: number;
  ended_at: number | null;
  wave: number | null;
  coins: number | null;
  tier: number | null;
  abandoned: number;
  scan_count: number;
  tap_count: number;
}

export interface Snapshot {
  name: string;
  ts: number;
  url: string;
}
```

- [ ] **Step 6: Write the formatters**

Create `web/ui/lib/format.ts`:

```ts
import type { BotEvent } from "./types";

export const clock = (ts: number): string =>
  new Date(ts * 1000).toTimeString().slice(0, 8);

export const money = (n: number | null | undefined): string =>
  n === null || n === undefined ? "-" : "$" + n;

/** One event as one line of the feed. */
export function describe(event: BotEvent): string {
  switch (event.type) {
    case "Tapped":
      return `TAP    ${event.action} (${event.x},${event.y}) score=${event.score.toFixed(3)} price=${money(event.price)}`;
    case "Skipped":
      return `SKIP   ${event.action} reason=${event.reason}${event.detail ? " " + event.detail : ""}`;
    case "ScreenChanged":
      return `SCREEN ${event.prev} -> ${event.curr} (${event.confidence.toFixed(3)})`;
    case "ScanCompleted":
      return `SCAN   ${event.screen} ${Math.round(event.duration_ms)}ms wallet=${money(event.wallet)}`;
    case "RunStarted":
      return `RUN    #${event.run_id} started`;
    case "RunEnded":
      return `RUN    #${event.run_id} ${event.abandoned ? "abandoned" : "ended"} wave=${event.wave ?? "?"} coins=${event.coins ?? "?"}`;
    case "Navigated":
      return `NAV    ${event.target}`;
    case "UnknownScreen":
      return `UNKNWN best=${event.best_anchor} ${event.best_score.toFixed(3)}`;
    case "BotError":
      return `ERROR  ${event.message}`;
    default:
      // An event type the UI predates. Showing its name beats dropping it.
      return (event as { type: string }).type;
  }
}
```

- [ ] **Step 7: Write the API client**

Create `web/ui/lib/api.ts`:

```ts
import type { RunRow, Snapshot, StatusPayload, StoredEvent } from "./types";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return (await response.json()) as T;
}

export const fetchStatus = () => getJson<StatusPayload>("/api/status");
export const fetchRuns = (limit = 30) => getJson<RunRow[]>(`/api/runs?limit=${limit}`);
export const fetchRunEvents = (id: number) => getJson<StoredEvent[]>(`/api/runs/${id}/events`);
export const fetchUnknown = () => getJson<Snapshot[]>("/api/unknown");
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd web/ui && npm test`
Expected: 5 passed.

- [ ] **Step 9: Commit**

```bash
git add web/ui/lib web/ui/vitest.config.ts web/ui/package.json web/ui/package-lock.json
git commit -m "feat: add typed API client and event formatting for the dashboard"
```

---

### Task 6: The live event stream hook

**Files:**
- Create: `web/ui/lib/useEventStream.ts`, `web/ui/lib/eventReducer.ts`, `web/ui/lib/eventReducer.test.ts`

**Interfaces:**
- Consumes: `BotEvent` from Task 5.
- Produces: `feedReducer(state, action)` and `useEventStream(): { events: BotEvent[]; connected: boolean }`.

- [ ] **Step 1: Write the failing test**

Create `web/ui/lib/eventReducer.test.ts`:

```ts
import { expect, it } from "vitest";
import { FEED_LIMIT, feedReducer } from "./eventReducer";
import type { BotEvent } from "./types";

const scan = (seq: number): BotEvent => ({
  type: "ScanCompleted", seq, ts: seq, screen: "IN_RUN", duration_ms: 40, wallet: null,
});

it("appends events in arrival order", () => {
  const state = feedReducer(feedReducer([], { kind: "event", event: scan(1) }), {
    kind: "event", event: scan(2),
  });
  expect(state.map((e) => e.seq)).toEqual([1, 2]);
});

it("drops the oldest beyond the limit so a long session cannot grow forever", () => {
  let state: BotEvent[] = [];
  for (let seq = 1; seq <= FEED_LIMIT + 10; seq += 1) {
    state = feedReducer(state, { kind: "event", event: scan(seq) });
  }
  expect(state).toHaveLength(FEED_LIMIT);
  expect(state[0].seq).toBe(11);
});

it("ignores an event it has already seen, so a reconnect replay cannot double up", () => {
  // EventSource replays from Last-Event-ID on reconnect, and the server's ring
  // is inclusive of anything the browser may already hold.
  let state = feedReducer([], { kind: "event", event: scan(1) });
  state = feedReducer(state, { kind: "event", event: scan(1) });
  expect(state).toHaveLength(1);
});

it("clears on demand", () => {
  const state = feedReducer([scan(1)], { kind: "clear" });
  expect(state).toEqual([]);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web/ui && npm test`
Expected: FAIL — `Failed to resolve import "./eventReducer"`.

- [ ] **Step 3: Write the reducer**

Create `web/ui/lib/eventReducer.ts`:

```ts
import type { BotEvent } from "./types";

/** Matches the server's SSE ring, so the browser never holds more than it. */
export const FEED_LIMIT = 500;

export type FeedAction =
  | { kind: "event"; event: BotEvent }
  | { kind: "clear" };

export function feedReducer(state: BotEvent[], action: FeedAction): BotEvent[] {
  if (action.kind === "clear") return [];

  // seq is monotonic and assigned by the bus, so it is the identity here.
  // A reconnect replays from Last-Event-ID and can resend what we already
  // hold; without this the feed shows each replayed event twice.
  const last = state.length ? state[state.length - 1].seq : 0;
  if (action.event.seq <= last) return state;

  const next = [...state, action.event];
  return next.length > FEED_LIMIT ? next.slice(next.length - FEED_LIMIT) : next;
}
```

- [ ] **Step 4: Write the hook**

Create `web/ui/lib/useEventStream.ts`:

```ts
"use client";

import { useEffect, useReducer, useState } from "react";
import { feedReducer } from "./eventReducer";
import type { BotEvent } from "./types";

/** The single SSE subscription. One per tab - mount this once, share via context.
 *
 * EventSource reconnects on its own and replays Last-Event-ID, so a dropped
 * connection costs nothing as long as the gap fits inside the server's ring.
 */
export function useEventStream(): { events: BotEvent[]; connected: boolean } {
  const [events, dispatch] = useReducer(feedReducer, []);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const source = new EventSource("/api/events/stream");
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message) =>
      dispatch({ kind: "event", event: JSON.parse(message.data) as BotEvent });
    return () => source.close();
  }, []);

  return { events, connected };
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web/ui && npm test`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add web/ui/lib
git commit -m "feat: add the dashboard's SSE hook and feed reducer"
```

---

### Task 7: The application shell

**Files:**
- Create: `web/ui/components/Sidebar.tsx`
- Modify: `web/ui/app/layout.tsx`, `web/ui/app/globals.css`

**Interfaces:**
- Produces: a sidebar layout wrapping every page, with links to `/`, `/runs/`, `/stats/`, `/errors/`, `/control/`.

Plans B and C add those pages. Linking to them now is deliberate: the nav is built once, and a link to a route that does not exist yet 404s visibly rather than being forgotten.

- [ ] **Step 1: Install shadcn/ui**

```bash
cd web/ui && npx --yes shadcn@latest init -d && npx --yes shadcn@latest add card badge button table input
```

- [ ] **Step 2: Write the sidebar**

Create `web/ui/components/Sidebar.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Live" },
  { href: "/runs/", label: "Runs" },
  { href: "/stats/", label: "Stats" },
  { href: "/errors/", label: "Errors" },
  { href: "/control/", label: "Control" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <nav className="flex shrink-0 gap-1 overflow-x-auto border-b p-2 md:h-dvh md:w-44 md:flex-col md:overflow-visible md:border-b-0 md:border-r md:p-3">
      <div className="hidden px-2 pb-3 text-sm font-semibold md:block">The Tower</div>
      {LINKS.map((link) => {
        const active = pathname === link.href;
        return (
          <Link
            key={link.href}
            href={link.href}
            className={`rounded-md px-3 py-1.5 text-sm ${
              active ? "bg-accent text-accent-foreground font-medium" : "text-muted-foreground hover:bg-accent/50"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 3: Wrap every page in it**

Replace the body of `web/ui/app/layout.tsx` with:

```tsx
import type { Metadata } from "next";
import { Sidebar } from "@/components/Sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "The Tower bot",
  description: "Live dashboard for the ADB bot",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="bg-background text-foreground antialiased">
        <div className="flex min-h-dvh flex-col md:flex-row">
          <Sidebar />
          <main className="min-w-0 flex-1 p-4">{children}</main>
        </div>
      </body>
    </html>
  );
}
```

Light and dark both come from the shadcn tokens `init` wrote into `globals.css`, which already switch on `prefers-color-scheme`. Do not add a theme toggle in this plan.

- [ ] **Step 4: Verify it builds and renders**

```bash
cd web/ui && npm run build && cd ../.. && uv run pytest tests/test_web_build.py -q
```
Expected: the build succeeds and the Python tests pass against the fresh output.

- [ ] **Step 5: Commit**

```bash
git add web/ui web/static
git commit -m "feat: add the dashboard's sidebar shell"
```

---

### Task 8: The Live page at parity

**Files:**
- Create: `web/ui/components/StatBar.tsx`, `web/ui/components/EventFeed.tsx`, `web/ui/components/RunTable.tsx`, `web/ui/components/SnapshotStrip.tsx`, `web/ui/components/WaveSparkline.tsx`
- Modify: `web/ui/app/page.tsx`
- Delete: `web/static/index.html` is replaced by the build; no manual deletion needed

**Interfaces:**
- Consumes: everything from Tasks 5 and 6.
- Produces: the Live page, matching today's `index.html` feature for feature.

Parity checklist, taken from the file being replaced. Every one of these must work before the task is done:

| Feature | Where |
|---|---|
| screen · uptime · scans · runs · wallet · dropped · last error | `StatBar` |
| current run: id, elapsed, per-action tap counts, else "idle" | `page.tsx` |
| wave sparkline over recent runs | `WaveSparkline` |
| live event feed, newest at the bottom, auto-scroll only when pinned | `EventFeed` |
| substring filter over the feed | `EventFeed` |
| click a run row → that run's stored events; "back to live" returns | `page.tsx` + `EventFeed` |
| run history table: # · wave · coins · tier · taps · length | `RunTable` |
| unknown-screen thumbnails, newest first, capped at 12 | `SnapshotStrip` |
| status every 2s, runs every 15s, snapshots every 60s | `page.tsx` |

- [ ] **Step 1: Write the feed**

Create `web/ui/components/EventFeed.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { clock, describe } from "@/lib/format";
import type { BotEvent } from "@/lib/types";

const TONE: Record<string, string> = {
  Tapped: "text-emerald-500",
  Skipped: "text-muted-foreground",
  ScreenChanged: "text-sky-500",
  Navigated: "text-sky-500",
  RunStarted: "text-amber-500",
  RunEnded: "text-amber-500",
  BotError: "text-red-500",
  UnknownScreen: "text-red-500",
};

export function EventFeed({ events }: { events: BotEvent[] }) {
  const [term, setTerm] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // Auto-scroll only when the reader is already at the bottom. Scrolling a
  // reader away from the line they are looking at is the fastest way to make
  // a live feed useless.
  useEffect(() => {
    const node = box.current;
    if (node && pinned.current) node.scrollTop = node.scrollHeight;
  }, [events]);

  const needle = term.trim().toLowerCase();
  const rows = events
    .map((event) => ({ event, line: `${clock(event.ts)} ${describe(event)}` }))
    .filter(({ line }) => needle === "" || line.toLowerCase().includes(needle));

  return (
    <div className="flex flex-col gap-2">
      <input
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        placeholder="filter…"
        className="w-48 rounded-md border bg-transparent px-2 py-1 text-sm"
      />
      <div
        ref={box}
        onScroll={(e) => {
          const node = e.currentTarget;
          pinned.current = node.scrollTop + node.clientHeight >= node.scrollHeight - 20;
        }}
        className="h-80 overflow-y-auto rounded-md border p-2 font-mono text-xs"
      >
        {rows.map(({ event, line }) => (
          <div key={event.seq} className={`whitespace-pre-wrap break-words ${TONE[event.type] ?? ""}`}>
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Write the stat bar**

Create `web/ui/components/StatBar.tsx`:

```tsx
import { money } from "@/lib/format";
import type { StatusPayload } from "@/lib/types";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

export function StatBar({ status, connected }: { status: StatusPayload | null; connected: boolean }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-sm">
        <span className={`inline-block size-2 rounded-full ${connected ? "bg-emerald-500" : "bg-red-500"}`} />
        {connected ? "live" : "reconnecting"}
        {status?.last_error ? <span className="ml-2 text-red-500">{status.last_error}</span> : null}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="screen" value={status?.screen ?? "-"} />
        <Stat label="uptime" value={status ? Math.round(status.uptime) + "s" : "-"} />
        <Stat label="scans" value={status?.scans ?? 0} />
        <Stat label="runs" value={status?.runs_completed ?? 0} />
        <Stat label="wallet" value={money(status?.wallet)} />
        <Stat label="dropped" value={status?.dropped ?? 0} />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Write the run table, sparkline and snapshot strip**

Create `web/ui/components/RunTable.tsx`:

```tsx
import type { RunRow } from "@/lib/types";

export function RunTable({ runs, onSelect }: { runs: RunRow[]; onSelect: (id: number) => void }) {
  if (!runs.length) {
    return <p className="text-sm text-muted-foreground">No stored runs. (Running with --no-store?)</p>;
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-muted-foreground">
        <tr>{["#", "wave", "coins", "tier", "taps", "length"].map((h) => (
          <th key={h} className="border-b py-1 text-left font-medium">{h}</th>
        ))}</tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.id} onClick={() => onSelect(run.id)} className="cursor-pointer hover:bg-accent/50">
            <td className="border-b py-1 tabular-nums">{run.id}</td>
            <td className="border-b py-1 tabular-nums">{run.wave ?? "-"}</td>
            <td className="border-b py-1 tabular-nums">{run.coins ?? "-"}</td>
            <td className="border-b py-1 tabular-nums">{run.tier ?? "-"}</td>
            <td className="border-b py-1 tabular-nums">{run.tap_count}</td>
            <td className="border-b py-1 tabular-nums">
              {run.ended_at ? Math.round(run.ended_at - run.started_at) + "s" : "live"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

Create `web/ui/components/WaveSparkline.tsx`. Inline SVG on purpose — Recharts arrives in Plan C, and pulling it in for one polyline would be the dependency doing the least work in the project:

```tsx
export function WaveSparkline({ waves }: { waves: number[] }) {
  if (waves.length < 2) {
    return <p className="text-sm text-muted-foreground">Not enough runs yet.</p>;
  }
  const top = Math.max(...waves) || 1;
  const points = waves
    .map((wave, i) => `${(i / (waves.length - 1)) * 100},${40 - (wave / top) * 36}`)
    .join(" ");
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-16 w-full">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1" className="text-sky-500" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
```

Create `web/ui/components/SnapshotStrip.tsx`:

```tsx
import type { Snapshot } from "@/lib/types";

export function SnapshotStrip({ shots }: { shots: Snapshot[] }) {
  if (!shots.length) {
    return <p className="text-sm text-muted-foreground">No unknown screens captured.</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {shots.slice(0, 12).map((shot) => (
        // Plain <img>: next/image needs a loader, and these are local PNGs
        // served by the same FastAPI process.
        // eslint-disable-next-line @next/next/no-img-element
        <img key={shot.name} src={shot.url} alt={shot.name}
             title={new Date(shot.ts * 1000).toLocaleString()}
             className="w-24 rounded border" />
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Write the Live page**

Replace `web/ui/app/page.tsx` entirely:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { EventFeed } from "@/components/EventFeed";
import { RunTable } from "@/components/RunTable";
import { SnapshotStrip } from "@/components/SnapshotStrip";
import { StatBar } from "@/components/StatBar";
import { WaveSparkline } from "@/components/WaveSparkline";
import { fetchRunEvents, fetchRuns, fetchStatus, fetchUnknown } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";
import type { BotEvent, RunRow, Snapshot, StatusPayload } from "@/lib/types";

/** Re-run `load` now and every `ms` thereafter, until unmounted. */
function usePoll(load: () => Promise<void>, ms: number) {
  useEffect(() => {
    let alive = true;
    const tick = () => { if (alive) void load(); };
    tick();
    const id = setInterval(tick, ms);
    return () => { alive = false; clearInterval(id); };
  }, [load, ms]);
}

export default function LivePage() {
  const { events, connected } = useEventStream();
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [shots, setShots] = useState<Snapshot[]>([]);
  // Non-null means the feed is showing one stored run instead of the live stream.
  const [history, setHistory] = useState<{ id: number; events: BotEvent[] } | null>(null);

  usePoll(useCallback(async () => setStatus(await fetchStatus()), []), 2000);
  usePoll(useCallback(async () => setRuns(await fetchRuns(30)), []), 15000);
  usePoll(useCallback(async () => setShots(await fetchUnknown()), []), 60000);

  async function showRun(id: number) {
    const stored = await fetchRunEvents(id);
    // Stored rows are columns plus a detail blob; flatten them back into the
    // shape describe() takes, so one renderer serves live and history both.
    // Blob last: it holds no column's name except `detail` itself, whose
    // string value is the one the renderer wants back.
    setHistory({ id, events: stored.map((row) => ({ ...row, ...row.detail }) as unknown as BotEvent) });
  }

  const waves = runs.filter((r) => r.wave != null).map((r) => r.wave as number).reverse();

  return (
    <div className="flex flex-col gap-4">
      <StatBar status={status} connected={connected} />

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border p-3">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Current run</h2>
          {status?.run ? (
            <p className="font-mono text-sm">
              #{status.run.id} · {Math.round(status.run.elapsed)}s ·{" "}
              {Object.entries(status.run.taps).map(([k, v]) => `${k} x${v}`).join(" · ") || "no taps yet"}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">idle</p>
          )}
        </section>

        <section className="rounded-lg border p-3">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Waves</h2>
          <WaveSparkline waves={waves} />
        </section>
      </div>

      <section className="rounded-lg border p-3">
        <h2 className="mb-2 flex items-center gap-3 text-xs uppercase tracking-wide text-muted-foreground">
          Events
          {history ? (
            <>
              <span>— run #{history.id}</span>
              <button onClick={() => setHistory(null)} className="rounded border px-2 py-0.5 normal-case">
                back to live
              </button>
            </>
          ) : null}
        </h2>
        <EventFeed events={history ? history.events : events} />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border p-3">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Run history</h2>
          <RunTable runs={runs} onSelect={showRun} />
        </section>

        <section className="rounded-lg border p-3">
          <h2 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Unknown screens</h2>
          <SnapshotStrip shots={shots} />
        </section>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Build and run every test**

```bash
cd web/ui && npm test && npm run build && cd ../.. && uv run pytest -q
```
Expected: vitest green, build succeeds, full Python suite green — including the freshness test, which now compares against the build you just made.

- [ ] **Step 6: Verify parity by eye**

```bash
uv run tower_bot.py --web --no-store
```
Open `http://127.0.0.1:8765`. Walk the parity checklist above. With `--no-store` the run table and history correctly show their empty states; run again without it to check the table, the sparkline and clicking a row into history mode.

- [ ] **Step 7: Commit**

```bash
git add web/ui web/static
git commit -m "feat: rebuild the dashboard's live page in React"
```

---

## Done when

- `uv run pytest -q` passes, including the build-freshness test.
- `npm test` in `web/ui` passes.
- `uv run tower_bot.py --web` serves the React dashboard on `127.0.0.1:8765` with no node running, and every row of Task 8's parity table works.
- `npm run dev` in `web/ui` serves the same UI on `:3000` with HMR, reading live data from a bot running on `:8765`.
- `web/static/` is committed and its manifest matches `web/ui/`.
- `/api/status`, `/api/runs`, `/api/runs/{id}/events`, `/api/events/stream` and `/api/unknown` all still work — the static mount did not swallow them.
- The old hand-written `web/static/index.html` is gone, replaced by the build.

## Follow-on plans

- **Plan B** — `docs/superpowers/plans/2026-09-01-dashboard-b-control.md`: the control plane.
- **Plan C** — `docs/superpowers/plans/2026-09-01-dashboard-c-views.md`: live device screen, run drill-down, stats, errors.
