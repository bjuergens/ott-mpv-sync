"""Launch mpv with a JSON IPC socket and drive it over that socket.

We own mpv's whole lifecycle: spawn it idle with --input-ipc-server, connect to
the unix socket, and send fire-and-forget commands. A reader thread mirrors the
few properties we care about (time-pos, pause) and detects mpv exiting (socket
EOF). All translation logic lives in `follower`; this module is pure transport.
"""

import json
import os
import re
import socket
import subprocess
import threading
import time

from .utils import OttSyncError, error, ok, warn

MIN_MPV = (0, 37)  # 3-arg loadfile / start= option verified from this version
# mpv 0.38 inserted an <index> arg into loadfile, between <flags> and <options>:
#   <=0.37: loadfile <url> <flags> <options>
#   >=0.38: loadfile <url> <flags> <index> <options>
# so passing an options string in the old position makes new mpv try to parse it
# as the integer index ("argument index can't be parsed").
LOADFILE_INDEX_MPV = (0, 38)
_SOCKET_TIMEOUT = 5.0  # seconds to wait for mpv to create the IPC socket


class Mpv:
    def __init__(self, mpv_bin: str, socket_path: str, extra_args=()):
        self.mpv_bin = mpv_bin
        self.sock_path = socket_path
        self.extra_args = list(extra_args)
        self.proc: subprocess.Popen | None = None
        self.conn: socket.socket | None = None
        self._rid = 0
        self._pending: dict[int, tuple] = {}
        self.mirror = {"time-pos": None, "pause": None}
        self.version: tuple[int, int] | None = None  # set by _probe_version()
        self.closed = threading.Event()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._check_socket_path()
        self._probe_version()
        # A normal, visible mpv (the user watches it), idle until the room gives
        # us a source.
        self.proc = subprocess.Popen(
            [
                self.mpv_bin,
                "--idle=yes",
                "--force-window=yes",
                "--keep-open=yes",
                f"--input-ipc-server={self.sock_path}",
                *self.extra_args,
            ]
        )
        self._wait_for_socket()
        self.conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.conn.connect(self.sock_path)
        threading.Thread(target=self._reader, daemon=True, name="mpv-ipc").start()
        # Observe the props we mirror for the seek-threshold check.
        self.command("observe_property", 1, "pause")
        self.command("observe_property", 2, "time-pos")
        ok(f"mpv started, IPC at {self.sock_path}")

    def _check_socket_path(self) -> None:
        if os.path.exists(self.sock_path):
            raise OttSyncError(f"IPC socket already exists: {self.sock_path}. Remove it or pass a different --socket.")
        parent = os.path.dirname(self.sock_path) or "."
        if not os.path.isdir(parent):
            raise OttSyncError(f"IPC socket directory does not exist: {parent}")
        if not os.access(parent, os.W_OK):
            raise OttSyncError(f"cannot create IPC socket in {parent} (not writable)")

    def _probe_version(self) -> None:
        """Read `mpv --version` into self.version; warn if older than MIN_MPV.

        self.version stays None if the probe fails or output is unrecognized;
        loadfile() then assumes the pre-0.38 (no <index>) form.
        """
        try:
            out = subprocess.run(
                [self.mpv_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError) as e:
            warn(f"could not probe mpv version: {e}")
            return
        m = re.search(r"mpv\s+v?(\d+)\.(\d+)", out)
        if not m:
            return
        self.version = (int(m.group(1)), int(m.group(2)))
        if self.version < MIN_MPV:
            warn(f"mpv {self.version[0]}.{self.version[1]} is older than {MIN_MPV[0]}.{MIN_MPV[1]}; seeking/loadfile may misbehave.")

    def loadfile(self, url: str, flags: str = "replace", options: str = "") -> None:
        """loadfile, papering over the 0.38 <index> arg insertion.

        `options` is a comma-separated mpv option string (e.g. "start=5,sub-files-append=..").
        On mpv >= 0.38 we must wedge an <index> before it; -1 is mpv's documented
        default (ignored for the `replace` flag we use). When the version is unknown
        we assume the old form, matching MIN_MPV.
        """
        args = ["loadfile", url, flags]
        if options:
            if self.version is not None and self.version >= LOADFILE_INDEX_MPV:
                args.append(-1)  # <index>: ignored by `replace`, just a placeholder
            args.append(options)
        self.command(*args)

    def _wait_for_socket(self) -> None:
        deadline = time.monotonic() + _SOCKET_TIMEOUT
        while time.monotonic() < deadline:
            if os.path.exists(self.sock_path):
                return
            code = self.proc.poll()
            if code is not None:
                raise OttSyncError(f"mpv exited during startup (exit code {code})")
            time.sleep(0.05)
        raise OttSyncError(f"mpv IPC socket never appeared at {self.sock_path} within {_SOCKET_TIMEOUT:g}s")

    # -- IPC ---------------------------------------------------------------
    def command(self, *args) -> None:
        """Fire-and-forget JSON IPC command. Failures are logged by the reader."""
        if self.conn is None:
            return
        self._rid += 1
        self._pending[self._rid] = args
        frame = json.dumps({"command": list(args), "request_id": self._rid})
        try:
            self.conn.sendall(frame.encode() + b"\n")
        except OSError as e:
            error(f"IPC send failed ({args[0] if args else '?'}): {e}")
            self.closed.set()

    def _reader(self) -> None:
        buf = b""
        while not self.closed.is_set():
            try:
                chunk = self.conn.recv(4096)
            except OSError as e:
                warn("OSError while reading: " + str(e))
                break
            if not chunk:
                warn("no chunk while reading.")
                break  # mpv closed the socket (window closed / quit)
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    # Never let one bad frame kill this thread silently: that would
                    # leave the follower running blind against an unobserved mpv.
                    try:
                        self._handle(json.loads(line))
                    except (json.JSONDecodeError, KeyError) as e:
                        warn(f"ignoring malformed mpv IPC frame ({type(e).__name__}: {e}): {line!r}")
        warn("mpv closed; shutting down")
        self.closed.set()

    def _handle(self, msg: dict) -> None:
        if msg.get("event") == "property-change":
            self.mirror[msg["name"]] = msg.get("data")
        elif "request_id" in msg:
            cmd = self._pending.pop(msg["request_id"], None)
            if msg.get("error") not in (None, "success"):
                error(f"mpv rejected {cmd}: {msg['error']}")

    def stop(self) -> None:
        self.closed.set()
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError as e:
                warn("OSError while closing: " + str(e))
                pass
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                warn("TimeoutExpired while terminating... now killing. ")
                self.proc.kill()
