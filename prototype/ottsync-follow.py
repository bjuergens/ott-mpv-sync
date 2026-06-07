#!/usr/bin/env python3
"""ottsync-follow — Option B prototype: a launcher that follows an OTT room.

Architecture (Option B, see research/2026-06-07 option-b-ipc.md):

    OTT room  --wss-->  [ this launcher ]  --unix socket (JSON IPC)-->  mpv

The launcher owns the whole lifecycle: it starts mpv with --input-ipc-server,
opens the OTT WebSocket, and translates room `sync` deltas into mpv commands
(loadfile / set pause / seek / set speed) per the §6 follower algorithm in
research/2026-06-07 initial.md. It NEVER sends control frames to the room.

Usage:
    prototype/ottsync-follow.py <roomName> [--host opentogethertube.com]

This is a research prototype, not the final plugin. It exists to evaluate
Option B end-to-end. Fail-loud logging per AGENTS.md.
"""
import argparse
import asyncio
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request

import websockets

SEEK_THRESHOLD = 3.0  # seconds; below this, let mpv's own clock run (±10s tol)


def log(emoji, msg):
    print(f"{emoji} {msg}", flush=True)


# --------------------------------------------------------------------------
# mpv side: launch mpv with an IPC socket and drive it over JSON IPC.
# --------------------------------------------------------------------------
class Mpv:
    def __init__(self, extra_args=()):
        self.sock_path = os.path.join(tempfile.mkdtemp(prefix="ottsync-"), "mpv.sock")
        self.extra_args = list(extra_args)
        self.proc = None
        self.conn = None
        self._rid = 0
        self._pending = {}            # request_id -> command (for error logging)
        self.mirror = {"time-pos": None, "pause": None}  # latest observed values
        self.closed = threading.Event()

    def start(self):
        # A normal, visible mpv (the user watches it), idle until the room
        # gives us a source. playback_only is N/A here since we own the process.
        self.proc = subprocess.Popen([
            "mpv", "--idle=yes", "--force-window=yes", "--keep-open=yes",
            f"--input-ipc-server={self.sock_path}",
        ] + self.extra_args)
        for _ in range(50):
            if os.path.exists(self.sock_path):
                break
            time.sleep(0.1)
        else:
            raise SystemExit("❌ mpv IPC socket never appeared")
        self.conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.conn.connect(self.sock_path)
        threading.Thread(target=self._reader, daemon=True).start()
        # Observe the props we mirror for the seek-threshold check.
        self.command("observe_property", 1, "pause")
        self.command("observe_property", 2, "time-pos")
        log("✅", f"mpv started, IPC at {self.sock_path}")

    def command(self, *args):
        """Fire-and-forget JSON IPC command. Errors are logged by the reader."""
        self._rid += 1
        self._pending[self._rid] = args
        frame = json.dumps({"command": list(args), "request_id": self._rid})
        try:
            self.conn.sendall(frame.encode() + b"\n")
        except OSError as e:
            log("❌", f"IPC send failed ({args[0]}): {e}")
            self.closed.set()

    def _reader(self):
        buf = b""
        while not self.closed.is_set():
            try:
                chunk = self.conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break                 # mpv closed the socket (window closed / quit)
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    self._handle(json.loads(line))
        log("⚠️", "mpv IPC socket closed")
        self.closed.set()

    def _handle(self, msg):
        if msg.get("event") == "property-change":
            self.mirror[msg["name"]] = msg.get("data")
        elif "request_id" in msg:
            cmd = self._pending.pop(msg["request_id"], None)
            if msg.get("error") not in (None, "success"):
                log("❌", f"mpv rejected {cmd}: {msg['error']}")

    def stop(self):
        self.closed.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


# --------------------------------------------------------------------------
# OTT side: connect, auth, translate sync deltas into mpv commands.
# --------------------------------------------------------------------------
def get_token(host):
    with urllib.request.urlopen(f"https://{host}/api/auth/grant") as r:
        return json.load(r)["token"]


class Follower:
    def __init__(self, mpv, host, room):
        self.mpv = mpv
        self.host = host
        self.room = room
        self.src_url = None           # local mirror of the loaded source
        self.playing = False

    def apply(self, delta):
        """Map one OTT `sync` delta onto mpv. Idempotent & action-keyed."""
        pos = delta.get("playbackPosition")   # may be absent (e.g. bare resume)

        # 1. source — detect a *new* url to (re)load
        new_url = None
        if "currentSource" in delta:
            cs = delta["currentSource"]
            if cs is None:
                self.mpv.command("stop")
                self.src_url = None
            else:
                url = cs.get("src_url") or cs["id"]   # `direct` -> id is the URL
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
            log("✅", f"loadfile {new_url} (start={pos})")
        elif pos is not None:
            # Standalone seek within an already-loaded file. Guard on a known
            # time-pos: if None, the file isn't ready yet (or just loaded with
            # start=), so skip — this also swallows the redundant post-join
            # position delta (see research §7.5).
            cur = self.mpv.mirror.get("time-pos")
            if cur is not None and abs(cur - pos) > SEEK_THRESHOLD:
                self.mpv.command("seek", pos, "absolute")
                log("✅", f"seek -> {pos:.2f}s (was {cur:.2f}s)")

    async def run(self):
        uri = f"wss://{self.host}/api/room/{self.room}"
        while not self.mpv.closed.is_set():
            try:
                async with websockets.connect(uri, open_timeout=10) as ws:
                    token = get_token(self.host)
                    await ws.send(json.dumps({"action": "auth", "token": token}))
                    log("✅", f"joined room {self.room} (follow-only)")
                    await self._listen(ws)
            except Exception as e:
                log("⚠️", f"OTT connection lost: {e!r}; reconnecting in 3s")
                await asyncio.sleep(3)

    async def _listen(self, ws):
        while not self.mpv.closed.is_set():
            raw = await ws.recv()
            frame = json.loads(raw)
            action = frame.get("action")
            if action == "sync":
                self.apply(frame)
            elif action == "unload":
                log("⚠️", "room unloaded; stopping")
                self.mpv.command("stop")
                return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("room")
    ap.add_argument("--host", default="opentogethertube.com")
    ap.add_argument("mpv_args", nargs="*",
                    help="extra args passed through to mpv (after `--`)")
    args = ap.parse_args()

    mpv = Mpv(extra_args=args.mpv_args)
    mpv.start()
    follower = Follower(mpv, args.host, args.room)

    # Stop the asyncio loop promptly when mpv exits (window closed / quit).
    async def supervise():
        loop = asyncio.get_event_loop()
        task = loop.create_task(follower.run())
        while not mpv.closed.is_set() and not task.done():
            await asyncio.sleep(0.3)
        task.cancel()

    try:
        asyncio.run(supervise())
    except KeyboardInterrupt:
        pass
    finally:
        mpv.stop()
        log("✅", "shut down")


if __name__ == "__main__":
    main()
