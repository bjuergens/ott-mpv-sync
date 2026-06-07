from ott_mpv_sync.follower import Follower
from ott_mpv_sync.roomurl import parse_room_url

EP = parse_room_url("https://host/room/r")


class FakeMpv:
    """Records IPC commands; `mirror` is settable to simulate observed state."""

    def __init__(self):
        self.commands = []
        self.mirror = {"time-pos": None, "pause": None}

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
