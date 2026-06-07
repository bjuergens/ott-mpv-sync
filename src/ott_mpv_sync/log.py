"""Emoji-prefixed logging per AGENTS.md: ✅ success, ⚠️ warning, ❌ error.

Deliberately tiny — plain stderr prints, flushed, so the user sees progress
interleaved with mpv's own terminal output in real time.
"""

import sys


def log(emoji: str, msg: str) -> None:
    print(f"{emoji} {msg}", file=sys.stderr, flush=True)


def ok(msg: str) -> None:
    log("✅", msg)


def warn(msg: str) -> None:
    log("⚠️", msg)


def error(msg: str) -> None:
    log("❌", msg)
