#!/usr/bin/env bash
# Smoke test for the phase-1 dummy plugin.
#
# Runs mpv in idle mode (no file) for a few seconds with the ottsync script
# loaded, captures the terminal log, and checks that heartbeats were emitted.
#
# Usage:
#   test/run-dummy.sh            # idle, ~4s
#   test/run-dummy.sh <file>     # play a media file instead of idle
#   DURATION=8 test/run-dummy.sh # run longer

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$REPO_ROOT/scripts/ottsync"
DURATION="${DURATION:-4}"
MEDIA="${1:-}"

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

echo "== running mpv with ottsync for ${DURATION}s =="

# --idle=yes keeps mpv alive with no file; --no-video/--vo=null avoid opening a
# window in a headless/CI context. --msg-level forces our script's info logs to
# show. We background mpv and kill it after DURATION so the test always returns.
mpv_args=(
    --no-config
    --idle=yes
    --vo=null
    --ao=null
    --msg-level=all=no,ottsync=info
    --script="$SCRIPT_DIR"
)

if [[ -n "$MEDIA" ]]; then
    mpv_args+=(--idle=no --keep-open=yes "$MEDIA")
fi

mpv "${mpv_args[@]}" >"$LOG" 2>&1 &
MPV_PID=$!

sleep "$DURATION"
kill "$MPV_PID" 2>/dev/null || true
wait "$MPV_PID" 2>/dev/null || true

echo "== captured output =="
cat "$LOG"
echo "====================="

HEARTBEATS="$(grep -c "heartbeat #" "$LOG" || true)"
if [[ "$HEARTBEATS" -ge 2 ]]; then
    echo "PASS: observed $HEARTBEATS heartbeat(s)"
    exit 0
else
    echo "FAIL: expected >=2 heartbeats, saw $HEARTBEATS"
    exit 1
fi
