"""Translate OTT room state into mpv commands. Follow-only — never sends control.

`apply(delta)` is the core: it maps one OTT `sync` delta onto mpv idempotently
(see research/2026-06-07 initial.md §6 and option-b-ipc.md for the verified
edge cases — start= on load, time-pos-guarded seek, idempotent isPlaying).
"""

import json

from websockets.exceptions import ConnectionClosed

from . import media, ott
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
        self._bootstrap: str | None = None  # first sync frame, applied before listening

    # -- translation -------------------------------------------------------
    def apply(self, delta: dict) -> None:
        """Map one OTT `sync` delta onto mpv. Idempotent & action-keyed."""
        pos = delta.get("playbackPosition")  # may be absent (e.g. bare resume)

        # 1. source — detect a *new* url to (re)load
        new_source = None  # a ResolvedSource (media url + subtitle tracks)
        if "currentSource" in delta:
            cs = delta["currentSource"]
            if cs is None:
                self.mpv.command("stop")
                self.src_url = None
            else:
                # Dedup on the room-provided url (`direct` -> id is the URL); a
                # custom-media manifest is then fetched/unwrapped to the real
                # media url + subtitles by media.resolve().
                url = cs.get("src_url") or cs["id"]
                if url != self.src_url:
                    new_source = media.resolve(cs)
                    self.src_url = url

        # 2. speed
        if "playbackSpeed" in delta:
            self.mpv.command("set_property", "speed", delta["playbackSpeed"])

        # 3. play / pause (set before loadfile so the new file inherits it)
        if "isPlaying" in delta:
            self.playing = delta["isPlaying"]
            self.mpv.command("set_property", "pause", not self.playing)

        # 4. position
        if new_source is not None:
            # Apply the initial position (`start=`) and any subtitle tracks
            # (`sub-files-append=`) as load options — atomic, so they can't race
            # the file-load the way separate `seek`/`sub-add` commands would.
            # (mpv 0.37 loadfile is 3-arg: <url> <flags> <options>; options are
            # comma-separated and the subtitle urls here contain no commas.)
            opts = []
            if pos is not None:
                opts.append(f"start={pos}")
            opts += [f"sub-files-append={s['url']}" for s in new_source.subtitles]
            if opts:
                self.mpv.command("loadfile", new_source.url, "replace", ",".join(opts))
            else:
                self.mpv.command("loadfile", new_source.url, "replace")
            # Re-assert the room's play state: --keep-open pauses mpv at the
            # previous file's EOF, and a source-change delta usually omits
            # isPlaying (unchanged in the room), so without this an auto-advanced
            # video would stay paused while the room keeps playing.
            self.mpv.command("set_property", "pause", not self.playing)
            subnote = f", {len(new_source.subtitles)} sub(s)" if new_source.subtitles else ""
            ok(f"loadfile {new_source.url} (start={pos}, playing={self.playing}{subnote})")
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
        room / rejected auth fails with a clean error and no stray mpv window.
        At startup *any* failure is fatal: there is no window worth keeping alive
        yet, so a transient open error becomes an OttSyncError too.
        """
        try:
            self._conn, self._bootstrap = ott.connect_and_auth(self.ep)
        except OttSyncError:
            raise  # grant / auth-rejection: already typed and fatal
        except Exception as e:
            raise OttSyncError(f"could not join room {self.ep.room!r} at {self.ep.host} ({type(e).__name__}: {e})") from e
        ok(f"joined room {self.ep.room} (follow-only)")

    def run(self) -> None:
        """Follow the room until mpv exits, reconnecting on transient drops.

        Assumes `connect()` established the first connection. The retry policy is
        keyed on the exception *type*, decided once at the transport boundary:

        * OttSyncError    -> fatal (bad token / rejected auth): re-raise and stop.
        * transport error -> a drop/timeout: log the specific error and reconnect
          (a fresh full sync re-bootstraps).
        * anything else   -> a real bug: let it propagate loudly (fail-fast).
        """
        while not self.mpv.closed.is_set():
            if self._conn is None:
                try:
                    self._conn, self._bootstrap = ott.connect_and_auth(self.ep)
                except OttSyncError:
                    raise  # rejected auth is fatal even mid-session
                # Retry only transport failures. Anything else (e.g. a KeyError
                # from a malformed frame) is a real bug — let it propagate loudly.
                except (ConnectionClosed, TimeoutError, OSError) as e:
                    warn(f"OTT reconnect failed ({type(e).__name__}: {e}); retrying in {_RECONNECT_DELAY:g}s")
                    self.mpv.closed.wait(_RECONNECT_DELAY)
                    continue
                ok(f"rejoined room {self.ep.room} (follow-only)")
            try:
                self._listen(self._conn)
                return  # _listen returned cleanly (unload / mpv closed)
            except (ConnectionClosed, TimeoutError, OSError) as e:
                warn(f"OTT connection lost ({type(e).__name__}: {e}); reconnecting in {_RECONNECT_DELAY:g}s")
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
        self._bootstrap = None

    def _listen(self, conn) -> None:
        # Apply the bootstrap sync captured at connect time before reading more,
        # so a reconnect re-initializes mpv from the room's live state.
        if self._bootstrap is not None:
            raw, self._bootstrap = self._bootstrap, None
            if not self._dispatch(raw):
                return
        while not self.mpv.closed.is_set():
            try:
                raw = conn.recv(timeout=_RECV_TIMEOUT)
            except TimeoutError:
                continue  # periodic wake to re-check mpv.closed
            if not self._dispatch(raw):
                return

    def _dispatch(self, raw) -> bool:
        """Handle one server frame. Returns False to stop the listen loop."""
        frame = json.loads(raw)
        action = frame.get("action")
        if action == "sync":
            self.apply(frame)
        elif action == "unload":
            warn("room unloaded; stopping")
            self.mpv.command("stop")
            return False
        return True
