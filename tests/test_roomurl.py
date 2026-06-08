import pytest

from ott_mpv_sync.roomurl import parse_room_url
from ott_mpv_sync.utils import OttSyncError


def test_https_room_derives_wss_and_grant():
    ep = parse_room_url("https://opentogethertube.com/room/abc123")
    assert ep.host == "opentogethertube.com"
    assert ep.room == "abc123"
    assert ep.base == ""
    assert ep.ws_url == "wss://opentogethertube.com/api/room/abc123"
    assert ep.grant_url == "https://opentogethertube.com/api/auth/grant"


def test_http_derives_ws():
    ep = parse_room_url("http://localhost/room/r")
    assert ep.ws_url == "ws://localhost/api/room/r"
    assert ep.grant_url == "http://localhost/api/auth/grant"


def test_trailing_slash_ignored():
    ep = parse_room_url("https://host/room/r/")
    assert ep.room == "r"
    assert ep.ws_url == "wss://host/api/room/r"


def test_base_path_preserved():
    ep = parse_room_url("https://host/watch/room/r")
    assert ep.base == "/watch"
    assert ep.ws_url == "wss://host/watch/api/room/r"
    assert ep.grant_url == "https://host/watch/api/auth/grant"


def test_port_preserved():
    ep = parse_room_url("http://localhost:8080/room/r")
    assert ep.host == "localhost:8080"
    assert ep.ws_url == "ws://localhost:8080/api/room/r"


def test_query_and_fragment_ignored():
    ep = parse_room_url("https://host/room/r?foo=bar#frag")
    assert ep.room == "r"
    assert ep.ws_url == "wss://host/api/room/r"


def test_uppercase_scheme_normalized():
    ep = parse_room_url("HTTPS://Host/room/r")
    assert ep.ws_url == "wss://host/api/room/r"


def test_encoded_room_name_decoded_and_reencoded():
    ep = parse_room_url("https://host/room/my%20room")
    assert ep.room == "my room"
    assert ep.ws_url == "wss://host/api/room/my%20room"


def test_last_room_segment_wins():
    ep = parse_room_url("https://host/room/outer/room/inner")
    assert ep.room == "inner"
    assert ep.base == "/room/outer"
    assert ep.ws_url == "wss://host/room/outer/api/room/inner"


@pytest.mark.parametrize(
    "url",
    [
        "foo",  # no scheme
        "ftp://host/room/r",  # non-http scheme
        "https://opentogethertube.com/foo/bar",  # no /room/
        "https://host/room/",  # empty room name
        "https:///room/r",  # no host
    ],
)
def test_invalid_urls_raise(url):
    with pytest.raises(OttSyncError):
        parse_room_url(url)
