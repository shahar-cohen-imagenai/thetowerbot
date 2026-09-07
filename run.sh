#!/usr/bin/env bash
# Sync deps, rebuild the dashboard if it is stale, then run the bot with the
# browser dashboard.
#
# The step that matters is the freshness check. `runtime_identity` freezes the
# backend's source hash at import and the built bundle records the backend it
# was built against, so editing Python invalidates the bundle without touching
# a single dashboard source file. Starting the bot then serves a dashboard that
# refuses to drive it - "Runtime mismatch - controls locked" - and the browser
# is a poor place to discover that. This checks before launching, and again
# after building, and fails here with both hashes instead.
#
# Node is only needed when the bundle is actually stale. A current bundle skips
# npm entirely, so `git clone && ./run.sh` works with no node installed - which
# is the whole reason web/static/ is committed. See README's Dashboard section.
#
# Extra flags pass through: ./run.sh --idle, ./run.sh --port 5556, etc.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

step() { printf '\033[1m[%s/4]\033[0m %s\n' "$1" "$2"; }
fail() { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

# --- 1. dependencies ------------------------------------------------------
step 1 'uv sync'
command -v uv >/dev/null 2>&1 || fail 'uv is not installed - see https://docs.astral.sh/uv/'
# --frozen: install exactly uv.lock, never silently re-resolve it. A start
# script that could rewrite the lockfile would make "it works on my machine"
# depend on who started the bot last.
uv sync --frozen --quiet

# --- 2. the dashboard bundle ---------------------------------------------
step 2 'dashboard freshness'
if uv run --quiet python -m tools.freshness >/dev/null 2>&1; then
    printf '      already current - skipping npm\n'
else
    reason=$(uv run --quiet python -m tools.freshness 2>/dev/null || true)
    printf '      %s\n' "$reason"
    command -v npm >/dev/null 2>&1 || fail \
        "the dashboard bundle is stale and npm is not installed.
       Install node, or check out a commit whose web/static/ matches its sources."
    ( cd web/ui && npm run build )
    # Re-checked, not assumed: a build that succeeds can still leave the
    # bundle mismatched (a partial publish, or sources changing underneath
    # it). Without this the script would hand the browser the very banner it
    # exists to prevent.
    uv run --quiet python -m tools.freshness >/dev/null 2>&1 || fail \
        "the bundle is STILL stale after building:
       $(uv run --quiet python -m tools.freshness 2>/dev/null || true)"
    printf '      rebuilt and verified\n'
fi

# --- 3. free the port -----------------------------------------------------
# Read from config.py rather than hardcoded, and overridden by a --port in the
# passed-through flags, so this never kills the wrong thing on the wrong port.
port=$(uv run --quiet python -c 'import config; print(config.WEB_PORT)')
args=("$@")
for i in "${!args[@]}"; do
    if [[ "${args[i]}" == '--port' && -n "${args[i+1]:-}" ]]; then
        port="${args[i+1]}"
    fi
done

step 3 "freeing port $port"
holders=$(lsof -ti "tcp:$port" 2>/dev/null || true)
if [[ -z "$holders" ]]; then
    printf '      nothing listening\n'
else
    for pid in $holders; do
        # Only ever a previous run of THIS bot. Something else on the port is
        # reported and left alone: a start script that killed by port number
        # alone would eventually take out an unrelated dev server.
        if ps -p "$pid" -o command= 2>/dev/null | grep -q 'tower_bot\.py'; then
            printf '      stopping previous bot (pid %s)\n' "$pid"
            kill "$pid" 2>/dev/null || true
            for _ in {1..50}; do
                ps -p "$pid" >/dev/null 2>&1 || break
                sleep 0.1
            done
            ps -p "$pid" >/dev/null 2>&1 && fail "pid $pid would not stop - kill it and retry"
        else
            fail "port $port is held by pid $pid, which is not this bot:
       $(ps -p "$pid" -o command= 2>/dev/null | head -1)"
        fi
    done
fi

# --- 4. run ---------------------------------------------------------------
step 4 "uv run tower_bot.py --web ${*:-}"
printf '\n  dashboard -> http://127.0.0.1:%s\n\n' "$port"
# exec, so Ctrl+C reaches the bot itself rather than this wrapper and the
# bot's own shutdown path runs.
exec uv run tower_bot.py --web "$@"
