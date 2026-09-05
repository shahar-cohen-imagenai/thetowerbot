# Tower autonomy execution instructions

You are continuing an existing implementation program. Treat this folder as the execution source of truth and the repository `AGENTS.md` as mandatory policy.

## Select work

1. Read `manifest.json`.
2. Keep every task with `status != "completed"` whose complete `depends_on` set appears in `completed_task_ids`.
3. Prefer `next_ready_task_ids` in listed order unless the user names another dependency-ready task.
4. Read only that task’s `tasks/<ID>.md` before exploring relevant code.
5. Seven tasks are dependency-ready. Take the one the user names, else the first
   entry of `next_ready_task_ids`. Do not create a graph ID that `manifest.json`
   does not already list.

Never infer completion from a source file, test, branch, or PR existing. The task acceptance gate must be proven and the implementation must be merged to `main`.

## Isolate every task

- Fetch `origin/main` and verify the exact base commit.
- Create `codex/<lowercase-id>-<short-description>` with `--no-track` in a sibling worktree.
- Never combine two graph task IDs in one branch or worktree.
- Never modify the dirty `codex/autonomy-research` checkout.
- Before pushing, run `git branch -vv` and explicitly push `HEAD:refs/heads/<feature-branch>`.
- Never push directly to `main`.

## Implement safely

- Preserve existing work listed under “Current repository state”; resume from it.
- Start with the focused failing tests named in the task file.
- Run only tests added or changed for the task, plus a directly affected neighboring test file when justified. Never run a whole test directory or suite.
- Python test commands must include `-p no:allure_pytest` and use `/Users/shahar/BifrostProjects/thetowerbot/.venv/bin/python`.
- Use type hints on all functions and async/await for I/O.
- Keep unknown, locked, unavailable, maxed, and unreadable game states distinct. Never coerce missing observations to zero.
- Require fresh, screen-specific evidence before any tap. One transaction step may perform at most one verified device action.
- Stop spending when identity, geometry, target, wallet, or confirmation evidence is ambiguous.
- Keep raw private captures, account IDs, temporary screenshots, generated reports, and local helper files out of Git.
- If Python runtime or API behavior changes, rebuild the tracked production UI and verify the build manifest’s backend hash.

## Finish one task

1. Prove the task’s full acceptance gate with focused automated checks and any explicitly required live evidence.
2. Update `manifest.json`, `STATUS.md`, and `tasks/<ID>.md` only when the complete graph task is certified. A partial slice leaves the graph task unchecked and records the landed slice under current state.
3. Stage only relevant files and show `git diff --cached --stat` plus the focused test results.
4. The repository requires explicit user approval before committing. Ask after the finished diff is staged and reviewable.
5. After approval, commit, push the feature branch, open a PR against `main`, and report its URL.
6. Do not merge unless the user explicitly asks.

When several tasks become dependency-ready, they may run in parallel only in separate worktrees. Each worker owns one task ID and does not edit another worker’s branch.
