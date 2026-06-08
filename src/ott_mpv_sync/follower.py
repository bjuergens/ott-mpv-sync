"""Translate OTT room state into mpv commands. Follow-only — never sends control.

`apply(delta)` is the core: it maps one OTT `sync` delta onto mpv idempotently
(see research/2026-06-07 initial.md §6 and option-b-ipc.md for the verified
edge cases — start= on load, time-pos-guarded seek, idempotent isPlaying).
"""

import json

from . import ott
from .mpv import Mpv
from .roomurl import RoomEndpoints
from .utils import OttSyncError, ok, warn

SEEK_THRESHOLD = 3.0  # seconds; below this, let mpv's own clock run (±10s tolerance)
_RECONNECT_DELAY = 3.0
_RECV_TIMEOUT = 1.0  # wake the recv loop this often to observe mpv shutdown


class Follower:
    def __init__(self, mpv: Mpv, ep: RoomEndpoints):
        self.mpv = mpv
        self.ep = ep
        self.src_url: str | None = None  # local mirror of the loaded source
        self.playing = False
        self._conn = None  # active WebSocket connection (set by connect())

    # -- translation -------------------------------------------------------
    def apply(self, delta: dict) -> None:
        """Map one OTT `sync` delta onto mpv. Idempotent & action-keyed."""
        pos = delta.get("playbackPosition")  # may be absent (e.g. bare resume)

        # 1. source — detect a *new* url to (re)load
        new_url = None
        if "currentSource" in delta:
            cs = delta["currentSource"]
            if cs is None:
                self.mpv.command("stop")
                self.src_url = None
            else:
                url = cs.get("src_url") or cs["id"]  # `direct` -> id is the URL
                if url != self.src_url:
                    new_url = url

        # 2. speed
        if "playbackSpeed" in delta:
            self.mpv.command("set_property", "speed", delta["playbackSpeed"])

        # 3. play / pause (set before loadfile so the new file inherits it)
        if "isPlaying" in delta:
            self.playing = delta["isPlaying"]
            self.mpv.command("set_property", "pause", not self.playing)

        # 4. position
        if new_url is not None:
            # Apply the initial position via `start=` ON the load — atomic, so it
            # can't race the file-load the way a separate `seek` does. (mpv 0.37
            # loadfile is 3-arg: <url> <flags> <options>.)
            if pos is not None:
                self.mpv.command("loadfile", new_url, "replace", f"start={pos}")
            else:
                self.mpv.command("loadfile", new_url, "replace")
            self.src_url = new_url
            # Re-assert the room's play state: --keep-open pauses mpv at the
            # previous file's EOF, and a source-change delta usually omits
            # isPlaying (unchanged in the room), so without this an auto-advanced
            # video would stay paused while the room keeps playing.
            self.mpv.command("set_property", "pause", not self.playing)
            ok(f"loadfile {new_url} (start={pos}, playing={self.playing})")
        elif pos is not None:
            # Standalone seek within an already-loaded file. Guard on a known
            # time-pos: if None, the file isn't ready yet (or just loaded with
            # start=), so skip — this also swallows the redundant post-join
            # position delta (see research §7.5).
            cur = self.mpv.mirror.get("time-pos")
            if cur is not None and abs(cur - pos) > SEEK_THRESHOLD:
                self.mpv.command("seek", pos, "absolute")
                ok(f"seek -> {pos:.2f}s (was {cur:.2f}s)")

    # -- connection lifecycle ---------------------------------------------
    def connect(self) -> None:
        """Open the room WebSocket and authenticate — fail fast.

        Called BEFORE mpv launches, so a bad URL / unreachable server / missing
        room fails with a clean error and no stray mpv window.
        """
        try:
            self._conn = ott.connect_and_auth(self.ep)
        except OttSyncError:
            raise  # auth-grant failures are already typed/fatal
        except Exception as e:
            raise OttSyncError(f"could not join room {self.ep.room!r} at {self.ep.host}: {e}") from e
        ok(f"joined room {self.ep.room} (follow-only)")

    def run(self) -> None:
        """Follow the room until mpv exits, reconnecting on drops.

        Assumes `connect()` already established the first connection. A drop
        reconnects (a fresh full sync re-bootstraps); auth-grant failures during
        reconnect are fatal.
        """
        while not self.mpv.closed.is_set():
            if self._conn is None:
                try:
                    self._conn = ott.connect_and_auth(self.ep)
                    ok(f"rejoined room {self.ep.room} (follow-only)")
                except OttSyncError:
                    raise
                except Exception as e:
                    warn(f"OTT reconnect failed: {e!r}; retrying in {_RECONNECT_DELAY:g}s")
                    self.mpv.closed.wait(_RECONNECT_DELAY)
                    continue
            try:
                self._listen(self._conn)
                return  # _listen returned cleanly (unload / mpv closed)
            except Exception as e:
                warn(f"OTT connection lost: {e!r}; reconnecting in {_RECONNECT_DELAY:g}s")
                self.close()
                self.mpv.closed.wait(_RECONNECT_DELAY)
        self.close()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _listen(self, conn) -> None:
        while not self.mpv.closed.is_set():
            try:
                raw = conn.recv(timeout=_RECV_TIMEOUT)
            except TimeoutError:
                continue  # periodic wake to re-check mpv.closed
            frame = json.loads(raw)
            action = frame.get("action")
            if action == "sync":
                self.apply(frame)
            elif action == "unload":
                warn("room unloaded; stopping")
                self.mpv.command("stop")
                return
