"""Command-line entry point: validate everything, then follow the room.

    ott-mpv-sync <room-url> [--mpv PATH] [--socket PATH] [-- <extra mpv args>]

Expected failures (bad URL, missing mpv, unreachable server, ...) surface as a
single ❌ line with an actionable message and a clean exit code — never a
traceback. Unexpected exceptions propagate (fail-loud).
"""

import argparse
import os
import shutil
import tempfile

from . import __version__
from .follower import Follower
from .mpv import Mpv
from .roomurl import parse_room_url
from .utils import OttSyncError, error, ok, warn


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="ott-mpv-sync",
        description="Follow an OpenTogetherTube room with a local mpv (follow-only).",
        epilog="examples:\n  ott-mpv-sync https://opentogethertube.com/room/my-room\n  ott-mpv-sync https://ott.example.com/room/abc --mpv /usr/bin/mpv\n  ott-mpv-sync https://opentogethertube.com/room/my-room -- --fullscreen\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("room_url", help="full URL of the OTT room (from your browser)")
    p.add_argument(
        "--mpv",
        default="mpv",
        metavar="PATH",
        help="mpv binary to launch (default: mpv on PATH)",
    )
    p.add_argument(
        "--socket",
        default=None,
        metavar="PATH",
        help="mpv IPC socket path (default: an auto-generated temp path)",
    )
    p.add_argument(
        "mpv_args",
        nargs="*",
        metavar="-- MPV_ARG ...",
        help="extra arguments passed through to mpv (after a literal --)",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    ep = parse_room_url(args.room_url)

    mpv_bin = shutil.which(args.mpv)
    if not mpv_bin:
        raise OttSyncError(f"mpv not found: {args.mpv!r}. Install mpv or pass --mpv PATH.")

    socket_path = args.socket or os.path.join(tempfile.mkdtemp(prefix="ott-mpv-sync-"), "mpv.sock")

    mpv = Mpv(mpv_bin, socket_path, extra_args=args.mpv_args)
    follower = Follower(mpv, ep)
    # Join the room BEFORE opening mpv, so a bad URL / unreachable server / missing
    # room fails fast with no stray window.
    follower.connect()
    try:
        mpv.start()
        follower.run()
    finally:
        follower.close()
        mpv.stop()
        ok("shut down")
    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        return _run(args)
    except OttSyncError as e:
        error(str(e))
        return 1
    except KeyboardInterrupt:
        warn("interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
