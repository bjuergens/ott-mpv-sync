#!/usr/bin/env python3
"""Probe a live OpenTogetherTube room to validate the protocol research.

Passive: gets a token, opens the WS, sends exactly one `auth` frame, then prints
every frame received (pretty-printed, truncated) for DURATION seconds. Sends
nothing else. Verifies the documented bootstrap sequence (full sync -> you).
"""

import asyncio
import json
import sys
import time
import urllib.request

import websockets

HOST = "opentogethertube.com"
ROOM = sys.argv[1] if len(sys.argv) > 1 else "2850ebf4-c60b-478b-a306-8188f7d07890"
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0


def get_token():
    with urllib.request.urlopen(f"https://{HOST}/api/auth/grant") as r:
        return json.load(r)["token"]


def short(obj, n=600):
    s = json.dumps(obj, indent=2, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"\n  ...[+{len(s) - n} chars]"


async def main():
    token = get_token()
    print(f"== token: {token[:24]}... ({len(token)} chars)")
    uri = f"wss://{HOST}/api/room/{ROOM}"
    print(f"== connecting {uri}")
    async with websockets.connect(uri, open_timeout=10) as ws:
        await ws.send(json.dumps({"action": "auth", "token": token}))
        print("== sent auth frame; listening (sending nothing else)\n")
        t0 = time.monotonic()
        idx = 0
        while time.monotonic() - t0 < DURATION:
            remaining = DURATION - (time.monotonic() - t0)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            idx += 1
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[{idx}] non-JSON: {raw!r}")
                continue
            action = frame.get("action", "<none>")
            dt = time.monotonic() - t0
            print(f"[{idx}] +{dt:5.2f}s action={action} keys={list(frame.keys())}")
            if action in ("sync", "you", "unload"):
                print(short(frame))
            print()
        print(f"== done after {idx} frame(s)")


if __name__ == "__main__":
    asyncio.run(main())
