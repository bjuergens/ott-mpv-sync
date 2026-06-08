"""OpenTogetherTube transport: mint a token and open the room WebSocket.

Synchronous (websockets.sync) — we manage exactly one connection, so threads are
simpler than asyncio. This module only connects and authenticates; the follower
owns the receive loop and reconnect policy.
"""

import json
import urllib.error
import urllib.request

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection
from websockets.sync.client import connect as ws_connect

from .roomurl import RoomEndpoints
from .utils import OttSyncError

_OPEN_TIMEOUT = 10.0


def get_token(grant_url: str) -> str:
    """GET <grant_url> -> token string, or raise OttSyncError with a clear reason."""
    try:
        with urllib.request.urlopen(grant_url, timeout=_OPEN_TIMEOUT) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        raise OttSyncError(f"auth grant failed: HTTP {e.code} at {grant_url}") from e
    except urllib.error.URLError as e:
        raise OttSyncError(f"cannot reach OTT server at {grant_url}: {e.reason}") from e
    except OSError as e:
        # A bare OSError/TimeoutError (e.g. a read timeout) has no .reason attribute.
        raise OttSyncError(f"cannot reach OTT server at {grant_url}: {e}") from e
    except json.JSONDecodeError as e:
        raise OttSyncError(f"auth grant returned invalid JSON from {grant_url}") from e

    token = payload.get("token")
    if not token:
        raise OttSyncError(f"auth grant returned no token from {grant_url}")
    return token


def connect_and_auth(ep: RoomEndpoints) -> tuple[ClientConnection, str]:
    """Mint a token, open the room WS, send the auth frame, and confirm the join.

    OTT sends no auth-ack: on success it immediately pushes a full `sync`; on a
    rejected token it closes the socket without a frame (see research initial.md
    §1.4). So we read that first frame here — its arrival *is* the proof the auth
    was accepted. Returns ``(open_conn, bootstrap_frame)``; the caller applies the
    bootstrap and then keeps reading.

    Raises OttSyncError for every fatal, non-retryable cause (bad grant, rejected
    auth, silent server). Transport failures while *opening* the socket propagate
    as the underlying websockets/OS exceptions for the caller to classify.
    """
    token = get_token(ep.grant_url)
    conn = ws_connect(ep.ws_url, open_timeout=_OPEN_TIMEOUT)
    conn.send(json.dumps({"action": "auth", "token": token}))
    try:
        bootstrap = conn.recv(timeout=_OPEN_TIMEOUT)
    except ConnectionClosed as e:
        conn.close()
        raise OttSyncError(f"room {ep.room!r} rejected our auth and closed the connection ({type(e).__name__}: {e})") from e
    except TimeoutError as e:
        conn.close()
        raise OttSyncError(f"room {ep.room!r} accepted the socket but sent no data within {_OPEN_TIMEOUT:g}s — cannot confirm the join") from e
    return conn, bootstrap
