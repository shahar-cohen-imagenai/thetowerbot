# Tower autonomy plan for Claude Code

This folder is the resumable execution package for the Tower bot autonomy roadmap.

- `STATUS.md` shows all 63 graph tasks. Completed tasks are checked.
- `manifest.json` is the machine-readable task/dependency/status source.
- `tasks/<ID>.md` contains the implementation contract and focused verification checklist for one graph task.
- `graph.mmd` is the dependency graph with completed and currently ready nodes highlighted.
- `source-graph.json` preserves the audited 63-node, 229-edge graph used to build this package.
- `CLAUDE.md` tells Claude Code how to select and execute work safely.

## Current handoff

Five tasks are complete: B01, B02, B03, E01, and O01. Fifty-eight remain. B04 is partial and is the only dependency-ready task. Its next slice is the automatic read-only Stats collection flow.

## Start in Claude Code

Open the repository in Claude Code and send:

```text
Read claude-code/tower-autonomy/CLAUDE.md and claude-code/tower-autonomy/manifest.json. Resume the next dependency-ready task. Follow the task file exactly, use a dedicated branch and sibling worktree, run only focused tests, and stop for the repository-required commit approval after staging the finished change.
```

To request a specific task, replace “the next dependency-ready task” with its ID. Claude must refuse a task whose dependencies are not complete in `manifest.json`.

## Status updates

Do not mark a task complete because code exists or a partial slice merged. Change `status` to `completed` only after its full acceptance gate is verified and merged to `main`. Then update `completed_task_ids`, `next_ready_task_ids`, counts, the task file, and `STATUS.md` in the task’s PR.
