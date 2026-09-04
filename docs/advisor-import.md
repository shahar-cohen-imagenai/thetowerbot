# Advisor import format

The Effective Paths advisor reads a normalized recommendation table supplied by
the player. It does not recalculate a workbook, fetch links, evaluate formulas or
translate arbitrary Effective Paths worksheet layouts. Export or transcribe the
recommendations from your populated account sheet into this format. Preserve the
source's units and use `null` for unknown numbers. Do not fill missing account
inputs with guesses. Native Effective Paths recommendations for labs, ultimate
weapons and enhancements remain visible as advice; standard Workshop unlock
planning remains in your manually configured Workshop purchase plan.

Download JSON or CSV examples from the Strategy panel. They are deliberately
marked EXAMPLE, stale and incomplete, so they cannot be staged. Replace the source,
account, timestamps and recommendations with actual exported results. No example
is automatically imported.

## JSON schema version 1

```json
{
  "schema_version": 1,
  "source": {
    "name": "EXAMPLE — replace with source name",
    "version": "EXAMPLE_VERSION",
    "account_name": "EXAMPLE_ACCOUNT",
    "exported_at": 0,
    "account_snapshot_at": 0
  },
  "missing_inputs": ["Replace example with your account data"],
  "recommendations": [{
    "id": "health-1",
    "path": "health",
    "system": "lab",
    "upgrade": "Health",
    "current_value": 1,
    "target_value": 2,
    "value_kind": "level",
    "cost": null,
    "currency": "coins",
    "benefit": null
  }]
}
```

| Field | Accepted value |
| --- | --- |
| `source.url` | Optional absolute HTTPS source link |
| Source timestamps | Unix seconds; account snapshot ≤ export ≤ current time |
| `missing_inputs` | Distinct names of required account inputs still missing; `[]` when complete |
| `id` | Unique recommendation identifier within this import |
| `path` | `health`, `damage`, `economy` |
| `system` | `workshop`, `lab`, `ultimate_weapon`, `enhancement`, `other` |
| `upgrade` | Source upgrade name; recognized names and aliases map to the catalog |
| `upgrade_id` | Optional catalog ID; must agree with the name for staging |
| `current_value`, `target_value`, `cost`, `benefit` | Finite nonnegative number or `null`; benefit is the source's estimate, not computed here |
| `value_kind` | `level` or `stat`; `stat` means the displayed upgrade value, in its displayed units |
| `currency` | `coins`, `gems`, `stones`, `medals`, `time`, `other` |

Files are limited to 256 KiB and 200 recommendations. Duplicate IDs, duplicate
JSON keys, unknown fields, formula cells, negative/nonfinite numbers and malformed
timestamps are rejected. Failed imports preserve the previous valid import.

## Normalized CSV

Use the same recommendation field names as columns, including `upgrade_id` even
when blank. Repeat these metadata columns on every row:

```text
schema_version,source_name,source_version,source_url,account_name,exported_at,account_snapshot_at,missing_inputs
```

`missing_inputs` is semicolon-separated; nullable numbers and optional fields use
empty cells. All metadata must agree across rows. Use the downloadable CSV as the
complete header template. This is a plain table of exported values, without
spreadsheet formulas.

## Draft behavior

Only fresh, complete, recognized standard Workshop recommendations with coins,
known cost/current/target, and an improving displayed stat target can be staged.
Both account and export data expire after 24 hours. Level targets and unlock tiles
remain advisory. A stale import revision is rejected if another import replaced it.

The server appends supported recommendations after existing Workshop priorities.
Existing canonical matches, including disabled rules and aliases, retain their
targets and position. Correct a miscategorized existing rule manually. Where the
catalog identifies a prerequisite, it must be enabled in the draft or positively
observed in the Workshop. The resulting draft still requires the Strategy page's
Save action. Spending uses the existing OCR executor and configured gates.

Imports persist locally per strategy profile, beside the strategies directory in
`advisor.json`. Account labels and completeness are supplied by the export; this
adapter cannot independently verify them against a spreadsheet or game account.
