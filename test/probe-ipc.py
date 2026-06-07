#!/usr/bin/env python3
"""Empirically probe mpv's JSON IPC (--input-ipc-server) for Option B research.

Launches a headless idle mpv with an IPC socket, connects, and exercises the
protocol: command replies (request_id), get_property, observe_property events,
loadfile, set pause, seek. Prints every line the socket emits, classified.
"""
import json
import os
import socket
import subprocess
import tempfile
import threading
import time

SOCK = os.path.join(tempfile.mkdtemp(), "mpv.sock")


def main():
    # Launch mpv headless+idle with the IPC server. mpv creates the socket.
    mpv = subprocess.Popen(
        ["mpv", "--no-config", "--idle=yes", "--vo=null", "--ao=null",
         "--no-terminal", f"--input-ipc-server={SOCK}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # Wait for the socket file to appear.
    for _ in range(50):
        if os.path.exists(SOCK):
            break
        time.sleep(0.1)
    else:
        raise SystemExit("socket never appeared")

    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(SOCK)
    print(f"== connected to {SOCK}\n")

    # Reader thread: print every newline-delimited JSON the socket emits,
    # tagged as reply (has request_id/error) vs event (has 'event').
    def reader():
        buf = b""
        while True:
            try:
                chunk = c.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                msg = json.loads(line)
                if "event" in msg:
                    print(f"  EVENT  {json.dumps(msg)}")
                else:
                    print(f"  REPLY  {json.dumps(msg)}")

    threading.Thread(target=reader, daemon=True).start()

    rid = [0]

    def send(command, **extra):
        rid[0] += 1
        frame = {"command": command, "request_id": rid[0], **extra}
        print(f"SEND     {json.dumps(frame)}")
        c.sendall((json.dumps(frame) + "\n").encode())
        time.sleep(0.4)  # let the reply/events arrive before next print

    # --- exercise the protocol ---
    print("--- 1. read a property while idle ---")
    send(["get_property", "idle-active"])

    print("\n--- 2. subscribe to playback props (the follower's inputs/outputs) ---")
    send(["observe_property", 1, "pause"])
    send(["observe_property", 2, "time-pos"])

    print("\n--- 3. loadfile a generated 10s test clip ---")
    clip = make_clip()
    send(["loadfile", clip])
    time.sleep(1.5)  # let it load; watch time-pos events flow

    print("\n--- 4. the four follower verbs: pause, seek, speed, unpause ---")
    send(["set_property", "pause", True])
    send(["seek", 5, "absolute"])
    send(["set_property", "speed", 1.5])
    send(["set_property", "pause", False])
    time.sleep(1.0)

    print("\n--- 5. read current position back ---")
    send(["get_property", "time-pos"])

    print("\n== done; quitting mpv")
    send(["quit"])
    time.sleep(0.3)
    c.close()
    mpv.terminate()


def make_clip():
    path = os.path.join(tempfile.mkdtemp(), "clip.mp4")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=10:size=320x240:rate=5", "-pix_fmt", "yuv420p",
         path], check=True)
    return path


if __name__ == "__main__":
    main()
