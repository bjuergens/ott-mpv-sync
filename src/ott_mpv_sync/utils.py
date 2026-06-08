"""Shared utilities: the one expected-failure exception and emoji logging.

`cli.main` catches `OttSyncError`, logs it, and exits non-zero — so expected
failures produce a clean ❌ message, not a traceback. Anything else propagates
(fail-loud: a real bug should be loud). The user-facing message is set at the
raise site.

Logging is deliberately tiny — plain stderr prints, flushed, so the user sees
progress interleaved with mpv's own terminal output in real time. Emoji prefix
per AGENTS.md: ✅ success, ⚠️ warning, ❌ error.
"""

import sys


class OttSyncError(Exception):
    """Expected, user-facing failure. The message is shown verbatim to the user."""


def _log(emoji: str, msg: str) -> None:
    print(f"{emoji} {msg}", file=sys.stderr, flush=True)


def ok(msg: str) -> None:
    _log("✅", msg)


def warn(msg: str) -> None:
    _log("⚠️", msg)


def error(msg: str) -> None:
    _log("❌", msg)
