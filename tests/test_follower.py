import json
import threading

import pytest
from websockets.exceptions import ConnectionClosed

from ott_mpv_sync import ott
from ott_mpv_sync.follower import Follower
from ott_mpv_sync.roomurl import parse_room_url
from ott_mpv_sync.utils import OttSyncError

EP = parse_room_url("https://host/room/r")


class FakeMpv:
    """Records IPC commands; `mirror` is settable to simulate observed state."""

    def __init__(self):
        self.commands = []
        self.mirror = {"time-pos": None, "pause": None}
        self.closed = threading.Event()

    def command(self, *args):
        self.commands.append(args)


def make():
    mpv = FakeMpv()
    return mpv, Follower(mpv, EP)


def test_new_source_loads_with_start_option():
    mpv, f = make()
    f.apply(
        {
            "currentSource": {"service": "direct", "src_url": "http://x/v.mp4"},
            "playbackPosition": 5.0,
        }
    )
    assert ("loadfile", "http://x/v.mp4", "replace", "start=5.0") in mpv.commands


def test_new_source_reasserts_play_state_when_room_playing():
    # Regression: --keep-open pauses mpv at the prior file's EOF, and the
    # auto-advance delta omits isPlaying — the new video must still play.
    mpv, f = make()
    f.playing = True  # room is playing
    f.apply(
        {
            "currentSource": {"service": "direct", "id": "http://x/next.mp4"},
            "playbackPosition": 0.0,
        }
    )
    assert ("set_property", "pause", False) in mpv.commands


def test_new_source_reasserts_pause_when_room_paused():
    mpv, f = make()
    f.playing = False  # room is paused
    f.apply(
        {
            "currentSource": {"service": "direct", "id": "http://x/next.mp4"},
            "playbackPosition": 0.0,
        }
    )
    assert ("set_property", "pause", True) in mpv.commands


def test_direct_source_falls_back_to_id():
    mpv, f = make()
    f.apply(
        {
            "currentSource": {"service": "direct", "id": "http://x/by-id.mp4"},
            "playbackPosition": 0.0,
        }
    )
    assert ("loadfile", "http://x/by-id.mp4", "replace", "start=0.0") in mpv.commands


def test_source_null_stops_and_clears():
    mpv, f = make()
    f.src_url = "http://x/v.mp4"
    f.apply({"currentSource": None})
    assert ("stop",) in mpv.commands
    assert f.src_url is None


def test_is_playing_sets_pause():
    mpv, f = make()
    f.apply({"isPlaying": False})
    assert ("set_property", "pause", True) in mpv.commands
    mpv.commands.clear()
    f.apply({"isPlaying": True})
    assert ("set_property", "pause", False) in mpv.commands


def test_redundant_position_with_unknown_timepos_does_not_seek():
    mpv, f = make()
    mpv.mirror["time-pos"] = None  # file not ready
    f.apply({"playbackPosition": 7.0})
    assert not any(c[0] == "seek" for c in mpv.commands)


def test_standalone_seek_when_beyond_threshold():
    mpv, f = make()
    mpv.mirror["time-pos"] = 1.0
    f.apply({"playbackPosition": 9.0})
    assert ("seek", 9.0, "absolute") in mpv.commands


def test_no_seek_within_threshold():
    mpv, f = make()
    mpv.mirror["time-pos"] = 8.0
    f.apply({"playbackPosition": 9.0})  # |8-9| = 1 < 3
    assert not any(c[0] == "seek" for c in mpv.commands)


def test_speed_change():
    mpv, f = make()
    f.apply({"playbackSpeed": 1.5})
    assert ("set_property", "speed", 1.5) in mpv.commands


def test_same_source_twice_loads_once():
    mpv, f = make()
    src = {"currentSource": {"service": "direct", "id": "http://x/v.mp4"}, "playbackPosition": 2.0}
    f.apply(src)
    f.apply(src)  # redundant; time-pos still None
    loads = [c for c in mpv.commands if c[0] == "loadfile"]
    assert len(loads) == 1


def test_bare_resume_does_not_seek_or_load():
    mpv, f = make()
    f.apply({"isPlaying": True})  # no position, no source (resume)
    assert not any(c[0] in ("seek", "loadfile") for c in mpv.commands)


# -- connection loop ------------------------------------------------------
def _closed():
    e = ConnectionClosed(None, None)
    return e


class FakeConn:
    """Yields queued frames from recv(), then raises the queued exception."""

    def __init__(self, frames, then=None):
        self._frames = list(frames)
        self._then = then
        self.closed = False

    def recv(self, timeout=None):
        if self._frames:
            return self._frames.pop(0)
        if self._then is not None:
            raise self._then
        raise AssertionError("recv past end without a terminal exception")

    def close(self):
        self.closed = True


def test_bootstrap_applied_before_listen():
    # connect() captures the first sync; run() must apply it on the first listen.
    mpv, f = make()
    bootstrap = json.dumps(
        {
            "action": "sync",
            "currentSource": {"service": "direct", "id": "http://x/v.mp4"},
            "playbackPosition": 0.0,
        }
    )
    f._conn = FakeConn([], then=_closed())  # listen sees no live frames, then drops
    f._bootstrap = bootstrap
    # _listen applies the bootstrap, then recv raises ConnectionClosed (transient).
    with pytest.raises(ConnectionClosed):
        f._listen(f._conn)
    assert any(c[0] == "loadfile" for c in mpv.commands)
    assert f._bootstrap is None  # consumed exactly once


def test_unload_frame_stops_listen():
    mpv, f = make()
    conn = FakeConn([json.dumps({"action": "unload"})])
    f._listen(conn)  # returns cleanly on unload
    assert ("stop",) in mpv.commands


def test_rejected_auth_is_fatal(monkeypatch):
    mpv, f = make()

    def boom(ep):
        raise OttSyncError("room 'r' rejected our auth")

    monkeypatch.setattr(ott, "connect_and_auth", boom)
    with pytest.raises(OttSyncError):
        f.run()  # _conn is None -> reconnect -> fatal, not retried


def test_malformed_frame_propagates_not_swallowed():
    # A KeyError from a bad frame is a real bug: it must NOT be caught as transient.
    mpv, f = make()
    conn = FakeConn([json.dumps({"action": "sync", "currentSource": {"service": "direct"}})])
    # currentSource without src_url or id -> KeyError in apply()
    with pytest.raises(KeyError):
        f._listen(conn)
