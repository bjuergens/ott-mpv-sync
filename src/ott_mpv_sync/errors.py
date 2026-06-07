"""Typed errors for expected failure modes.

`cli.main` catches `OttSyncError`, logs it, and maps it to an exit code — so
expected failures produce a clean ❌ message, not a traceback. Anything that is
NOT one of these propagates (fail-loud: a real bug should be loud).

Exit codes: 2 = bad CLI input / room URL; 1 = runtime/setup failure.
"""


class OttSyncError(Exception):
    """Base class for expected, user-facing failures. `exit_code` drives sys.exit."""

    exit_code = 1


class RoomUrlError(OttSyncError):
    """The room URL could not be parsed into OTT endpoints."""

    exit_code = 2


class MpvError(OttSyncError):
    """mpv could not be found, launched, or reached over IPC."""

    exit_code = 1


class OttError(OttSyncError):
    """The OTT server could not be reached or refused us (auth/room)."""

    exit_code = 1
