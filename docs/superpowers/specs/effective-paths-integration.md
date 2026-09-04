# Effective Paths integration assessment

Status: import-based adapter implemented. Live spreadsheet syncing and formula
calculation remain outside this implementation. See `docs/advisor-import.md`.

Effective Paths is a community Google Sheets tool with linked account-input
spreadsheets and separate effective-health, damage and economy recommendation
outputs. Its current FAQ explicitly excludes a standard Workshop path. Workshop
stats are inputs, but early-game unlock planning remains the guide presets' job.

## Proposed control-room integration

1. Connect the player's own populated spreadsheet or accept an exported
   recommendation table. Record the source version, export time and account
   snapshot time. Do not use example values from the public template.
2. Show health, damage and economy recommendations in an advisory panel with
   current level, recommended next level, cost/currency, estimated benefit and
   source. Preserve unsupported recommendations as advisory-only.
3. Present missing/stale account inputs explicitly. Battle-modified stats are not
   Workshop levels; OCR values must not be written into level fields by inference.
   Labs, cards, modules, relics, perks and ultimate weapons need their own confirmed
   inputs. A missing value must not silently become zero.
4. Map recognized, supported recommendations into an editable strategy draft.
   Only the existing executor can spend, using current OCR evidence, prerequisites,
   target limits and the player's spending settings. Guide prerequisites take
   precedence over optional optimization recommendations.

Prefer a versioned import adapter to copying spreadsheet formulas into the bot.
The reviewed pages document spreadsheet/IDS setup; they do not establish a stable
recommendation API. Determine accessible output ranges and calculation freshness
against a personal copy before choosing an automatic connector. Keep spreadsheet
code and formulas out of the bot process.

## Inputs needed for a future live connector

- A personal Effective Paths spreadsheet or representative exported results.
- Confirmation of which path and account profile the results represent.
- Verified worksheet ranges and calculation freshness for that personal copy.

## Sources

- [Tool directory](https://the-tower.notion.site/Effective-Paths-1bb91383b93f80d5aed2c098cbbd9e46)
- [Setup guide](https://the-tower.notion.site/Guide-to-Setting-Up-and-Using-the-Effective-Paths-1e591383b93f80379b0ec8b94fc5ba0f)
- [Maintained spreadsheet and FAQ](https://docs.google.com/spreadsheets/d/1YwZtKP6B4WYhRba5T6APJ1YxKNdfnIGQnprgnxmO7zc/edit)
