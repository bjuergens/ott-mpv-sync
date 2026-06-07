"""OpenTogetherTube transport: mint a token and open the room WebSocket.

Synchronous (websockets.sync) — we manage exactly one connection, so threads are
simpler than asyncio. This module only connects and authenticates; the follower
owns the receive loop and reconnect policy.
"""

import json
import urllib.error
import urllib.request

from websockets.sync.client import ClientConnection
from websockets.sync.client import connect as ws_connect

from .errors import OttError
from .roomurl import RoomEndpoints

_OPEN_TIMEOUT = 10.0


def get_token(grant_url: str) -> str:
    """GET <grant_url> -> token string, or raise OttError with a clear reason."""
    try:
        with urllib.request.urlopen(grant_url, timeout=_OPEN_TIMEOUT) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        raise OttError(f"auth grant failed: HTTP {e.code} at {grant_url}") from e
    except (urllib.error.URLError, OSError) as e:
        raise OttError(f"cannot reach OTT server at {grant_url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise OttError(f"auth grant returned invalid JSON from {grant_url}") from e

    token = payload.get("token")
    if not token:
        raise OttError(f"auth grant returned no token from {grant_url}")
    return token


def connect_and_auth(ep: RoomEndpoints) -> ClientConnection:
    """Mint a token, open the room WS, and send the single auth frame.

    Returns an open connection (usable as a context manager). Raises OttError if
    the token cannot be obtained; other connection failures propagate as the
    underlying websockets/OS exceptions for the caller to classify.
    """
    token = get_token(ep.grant_url)
    conn = ws_connect(ep.ws_url, open_timeout=_OPEN_TIMEOUT)
    conn.send(json.dumps({"action": "auth", "token": token}))
    return conn
