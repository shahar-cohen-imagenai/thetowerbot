# Effective Paths Advisory Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development.

**Goal:** Import account-specific recommendations, show readiness and provenance,
and stage supported Workshop recommendations without directly spending.

**Architecture:** A strict versioned JSON/CSV adapter stores one import per local
strategy profile. FastAPI exposes snapshots and a pure draft transformation.
The Strategy page owns Save/Revert; importing or staging never controls the device.

**Tech Stack:** Python/FastAPI, local atomic JSON storage, Next.js/React.

**Spec:** `docs/superpowers/specs/effective-paths-integration.md`.

## Constraints and contract

Implement the import option from the approved design. Live Google Sheets syncing
requires a personal populated sheet and verified ranges; no connector is claimed.
The accepted CSV is a normalized recommendation table, not arbitrary native
Effective Paths worksheets. UI and documentation must make that clear. Do not
evaluate spreadsheet formulas or send account data externally. No live game actions,
commits or broad test runs. Preserve current work and the running bot.

Import JSON schema v1: `source` contains `name`, `version`, `account_name`,
`exported_at`, `account_snapshot_at` (Unix seconds), optional HTTPS `url`;
`missing_inputs` is a string list. `recommendations` contains `id`, `path`
(health/damage/economy), `system` (workshop/lab/ultimate_weapon/enhancement/other),
`upgrade`, optional `upgrade_id`, `current_value`, `target_value`, `value_kind`
(level/stat), `cost`, `currency` (coins/gems/stones/medals/time/other), and `benefit`.
Benefit is numeric or null; unknown numeric values are null. CSV uses the same row fields plus repeated source
columns `source_name`, `source_version`, `source_url`, `account_name`, `exported_at`,
`account_snapshot_at`; `missing_inputs` uses semicolons. Defaults never infer stat
values from levels. Limit files to 256 KiB, recommendations to 200, and use finite
nonnegative numbers. Reject duplicate IDs, inconsistent CSV metadata, formula cells,
unsupported schema versions and future/inverted timestamps. Data older than 24h is
stale and cannot be staged; missing inputs also block staging.

`AdvisorStore(path: Path|None)` provides import_file(profile, filename, content),
snapshot(profile), and get_import(profile). None uses memory for tests; app derives
the file beside its configured strategies directory. Imports are atomic; failed
imports preserve old data. Returned snapshots include import_id (content revision),
profile, imported_at, source, missing_inputs, stale, recommendations. Rows include
can_stage and blocked_reason. Only known standard Workshop/coins/stat targets with
known current/target/cost and an improving target can be staged; source identity
must resolve consistently. Unsupported rows remain visible.

API: GET /api/advisor?profile=...; POST /api/advisor/import with
{profile,filename,content}; POST /api/advisor/draft with
{profile,import_id,recommendation_id,draft}. The last returns {draft,added,message};
it checks current import revision/readiness, appends after all existing rules,
preserves existing rows and all settings, and does not save. Existing matches
return unchanged with a clear explanation. Template download is clearly labeled
example data and never automatically imported.

## Tasks

- [x] Parser/store: `advisor.py`, `tests/test_advisor.py`. Verify JSON/CSV parity,
  invalid/missing/stale inputs, atomic persistence, profile isolation and mapping.
- [x] Panel: `web/ui/components/AdvisorPanel.tsx` plus focused tests, types/API,
  Strategy integration. Import file or pasted normalized table, filter paths,
  show metadata/blocked reasons, stage supported rows into the draft, retain
  Save/Revert. Handle profile switches and failed requests without stale results.
- [x] API/draft bridge: `web/app.py`, `tests/test_advisor_api.py`. Verify read/import
  routes, revision rejection, safe append, duplicate handling and zero mutation of
  saved profiles or device controls.
- [x] Integration: focused tests and production build, isolated preview, review,
  README and example format. Report import capability and live-connector limits.

## Progress

All four implementation tasks are complete. Validation: 62 focused Python tests;
53 focused frontend tests, followed by 16 Strategy page tests after the navigation
link; production build, lint/type checks and static export passed. The idle preview
verified import, metadata, duplicate preservation, new draft addition and Revert.
Example data was removed afterward. Changes remain uncommitted. Live Google Sheets
syncing still requires a personal sheet and verified output ranges.
