"""Turn a room URL into the OTT endpoints we actually talk to.

A user pastes the URL they see in the browser, e.g.

    https://opentogethertube.com/room/2850ebf4-c60b-478b-a306-8188f7d07890

from which we derive the WebSocket endpoint and the auth-grant endpoint:

    wss://opentogethertube.com/api/room/2850ebf4-...
    https://opentogethertube.com/api/auth/grant

A deployment under a base path (e.g. https://host/watch/room/<name>) is handled
by preserving everything before the `room` segment as a base prefix.
"""

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

from .utils import OttSyncError


@dataclass(frozen=True)
class RoomEndpoints:
    host: str  # hostname[:port], no scheme
    room: str  # decoded room name (for display / identity)
    base: str  # path prefix before /room/, "" or "/sub"; no trailing slash
    ws_url: str  # wss://host[/base]/api/room/<name>
    grant_url: str  # https://host[/base]/api/auth/grant


def parse_room_url(url: str) -> RoomEndpoints:
    """Parse a room URL into endpoints, or raise OttSyncError with guidance."""
    parts = urlsplit(url.strip())

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise OttSyncError(
            f"room URL must start with http:// or https:// (got {url!r}). "
            "Paste the full room URL from your browser."
        )

    host = parts.hostname
    if not host:
        raise OttSyncError(f"room URL has no host: {url!r}")
    netloc = host if parts.port is None else f"{host}:{parts.port}"

    # Find the LAST `room` segment so a base path that itself contains "room"
    # (e.g. /myroom/.../room/<name>) resolves correctly.
    segments = [s for s in parts.path.split("/") if s]
    try:
        idx = len(segments) - 1 - segments[::-1].index("room")
    except ValueError:
        raise OttSyncError(f"room URL must contain a /room/<name> segment: {url!r}") from None
    if idx + 1 >= len(segments):
        raise OttSyncError(f"room URL is missing the room name after /room/: {url!r}")

    room = unquote(segments[idx + 1]).strip()
    if not room:
        raise OttSyncError(f"room URL has an empty room name: {url!r}")

    base = "/" + "/".join(segments[:idx]) if idx > 0 else ""
    ws_scheme = "wss" if scheme == "https" else "ws"
    name_enc = quote(room, safe="")

    return RoomEndpoints(
        host=netloc,
        room=room,
        base=base,
        ws_url=f"{ws_scheme}://{netloc}{base}/api/room/{name_enc}",
        grant_url=f"{scheme}://{netloc}{base}/api/auth/grant",
    )
