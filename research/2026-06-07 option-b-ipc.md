# Option B in depth: launcher + `mpv --input-ipc-server`

Follow-up to `2026-06-07 architecture.md`. Everything here is **verified empirically**
against mpv 0.37.0 with two throwaway probes (`test/probe-ipc.py`) and a working
end-to-end prototype (`prototype/ottsync-follow.py`) run against the live room.

**Option B topology** — the launcher owns everything; there is **no mpv Lua script**:

```
OTT room  ──wss──▶  [ ottsync-follow launcher ]  ──unix socket / JSON IPC──▶  mpv
                     (auth, translate sync→cmds)        (--input-ipc-server)
```

---

## 1. How `--input-ipc-server` works (verified)

`mpv --input-ipc-server=<path>` makes mpv create a **unix domain socket** at `<path>`
(a **named pipe** on Windows) and serve the **JSON IPC** protocol on it. An external
process connects and speaks **newline-delimited JSON**, one message per line.

**Request:**
```json
{"command": ["set_property", "pause", true], "request_id": 5}
```
**Reply** (echoes your `request_id`; `error` is `"success"` or a message):
```json
{"request_id": 5, "error": "success"}
{"data": 7.0, "request_id": 9, "error": "success"}      // get_property reply
{"request_id": 1, "error": "invalid parameter"}          // a rejection
```
**Events** (unsolicited; subscribe via `observe_property`):
```json
{"event": "file-loaded"}
{"event": "property-change", "id": 2, "name": "time-pos", "data": 5.0}
{"event": "end-file", "reason": "quit", "playlist_entry_id": 1}
```

Verified facts:
- **Bidirectional**: you get command replies *and* async events on the same socket.
- **`observe_property <id> <name>`** then streams `property-change` events. `time-pos`
  ticks ~5/s during playback; the `data` key is **absent** when the value is unavailable
  (idle / between files) — treat missing `data` as "unknown", not 0.
- **`get_property`** works synchronously (reply carries `data`).
- **No auth, no encryption** — access control is the socket's filesystem permissions.
  Local-only by design (per mpv manual; the `run` command can execute arbitrary processes).
- **mpv creates the socket asynchronously** after launch — poll for the file to appear
  before connecting (the prototype waits up to 5 s).
- **Socket EOF = mpv gone** (window closed / `quit`): `recv()` returns empty. This is the
  launcher's signal to shut down.

### The follower's entire command vocabulary (all verified)
| Intent | IPC command |
|--------|-------------|
| Load source | `["loadfile", <url>, "replace", "start=<pos>"]` |
| Pause / resume | `["set_property", "pause", true/false]` |
| Seek (loaded file) | `["seek", <abs>, "absolute"]` |
| Speed | `["set_property", "speed", <x>]` |
| Stop (source→null) | `["stop"]` |
| Read head (threshold) | observe `time-pos`, or `["get_property","time-pos"]` |

---

## 2. What the launcher script looks like

See `prototype/ottsync-follow.py` (runnable). Shape:

```
class Mpv:
    start():   Popen(["mpv","--idle=yes","--force-window=yes","--keep-open=yes",
                      "--input-ipc-server=<sock>", *passthrough])
               wait for <sock>; connect AF_UNIX; spawn reader thread;
               observe_property pause + time-pos
    command(*args):  send {"command":[...], "request_id":n}\n   (fire-and-forget)
    reader thread:   parse lines → update self.mirror{time-pos,pause};
                     log ❌ on any reply whose error != success;
                     on socket EOF set self.closed   (mpv exited)

class Follower:                       # the §6 algorithm from initial.md
    run():    loop: get_token(); wss connect; send one {"action":"auth",...};
              listen; on drop, reconnect (fresh full sync re-bootstraps)
    apply(delta):                     # idempotent, action-keyed
        currentSource → new url? remember it
        playbackSpeed → set_property speed
        isPlaying     → set_property pause (before loadfile, so file inherits it)
        new url       → loadfile url replace start=<pos>      # atomic initial pos
        else pos      → seek abs, but only if time-pos known & |Δ|>3s

main():  Mpv.start(); asyncio.run(supervise())   # supervise stops the ws
         task the moment Mpv.closed flips (mpv window closed)
```

Run it:
```sh
prototype/ottsync-follow.py <roomName> [--host opentogethertube.com] [-- <extra mpv args>]
# headless test (no window):
prototype/ottsync-follow.py <room> -- --vo=null --ao=null --no-terminal
```

Threading model: the **IPC reader runs in a thread** (maintains the property mirror
and logs failures); the **OTT wss loop is asyncio** in the main thread; IPC sends are
plain `sendall` (safe from the loop thread). The follower reads the mirrored `time-pos`
(no blocking round-trip) for the seek-threshold check.

---

## 3. Findings from building it (the non-obvious bits)

1. **A `seek` right after `loadfile` is REJECTED** — `{"error":"error running command"}` —
   because the file hasn't loaded yet (no timeline to seek on). First prototype hit this
   on every join. **Fix: set the initial position with `start=<pos>` as a loadfile
   *option*, not a separate seek.** It applies atomically when the file opens, no race.
2. **mpv 0.37 `loadfile` is 3-arg: `<url> <flags> <options>`.** `["loadfile",url,"replace","start=5.5"]`
   works; the 4-arg form with an index (`...,"replace","-1","start=..."`) returns
   `"invalid parameter"` (that's the 0.38+ signature). **Pin the signature to the mpv version.**
3. **Standalone seeks must be guarded on a known `time-pos`.** If `time-pos` is `None`
   (file not ready, or just loaded via `start=`), skip the seek. This single guard also
   cleanly **swallows the redundant post-join `{playbackPosition}` delta** documented in
   `initial.md` §7.5 — which otherwise fired a second, racing seek.
4. **Set `pause` before `loadfile`** so the freshly loaded file inherits the room's
   play/pause state (the join sync carries `isPlaying`).
5. **`time-pos` event omits `data` when unavailable** — guard for the key, don't assume 0.

After applying 1–5, a live join produces exactly:
```
✅ mpv started, IPC at /tmp/ottsync-…/mpv.sock
✅ joined room … (follow-only)
✅ loadfile https://bortec.eu:18123/video/qqq.mp4 (start=3.672)
```
…with **no rejected commands** and no spurious seeks.

---

## 4. Lifecycle & edge cases the launcher must own

| Event | Handling (in prototype) |
|-------|-------------------------|
| mpv window closed / quit | socket EOF → `closed` set → supervise cancels ws task → exit |
| OTT socket drop | reconnect loop; fresh **full sync** re-bootstraps state |
| `unload` frame | `stop`; stop following |
| source → `null` | `stop`, clear local `src_url` |
| Ctrl-C | terminate mpv, clean exit |

Still **not** handled (would matter for the real thing): private/locked-room auth
(Q2, untested), subtitle `sub-add`, HLS/DASH, and a tighter `start=` that compensates
for load latency (`pos + (ready−recv)`) — unnecessary at ±10 s.

---

## 5. Assessment: Option B vs Option A

**What Option B gets right:** it is the *simplest* design that works — one process, one
language, all logic in Python with a real WS+TLS stack, and the IPC protocol is trivial
and fully verified. The prototype already follows the live room correctly. No mpv script,
no LuaJIT/FFI questions, no streaming-stdout trap.

**The cost is UX/ownership:** the user runs **our launcher** (`ottsync-follow <room>`),
not their normal `mpv`. We choose mpv's args and own its window/lifecycle. It composes
poorly with a user who has a finely-tuned `mpv.conf` invocation or launches mpv from a
file manager — they'd have to go through us instead. (We *can* pass through extra mpv
args, as the prototype does with `-- …`, which softens this.) An **attach mode** —
connect to an *already running* mpv that was started with `--input-ipc-server` — is a
natural extension that recovers the "use your own mpv" workflow; not yet built.

**Versus Option A** (mpv Lua script spawns the helper, mpv-discord pattern): A keeps the
native "install a script, just run mpv" UX and the *exact same* helper code can drive mpv
over IPC — A is essentially B with the ownership inverted (mpv launches the helper instead
of the helper launching mpv). The IPC mechanics proven here apply unchanged to A.

**Bottom line:** Option B is fully de-risked and the fastest path to something usable; the
only open decision it leaves is whether the launcher-owns-mpv UX is acceptable, or whether
we want the script-owns-launch UX of A (same IPC core, ~15 lines of Lua glue) or an attach
mode. The translation/lifecycle code is identical across all three.

---

## Sources
- Verified firsthand on mpv 0.37.0, 2026-06-07: `test/probe-ipc.py` (IPC protocol),
  `prototype/ottsync-follow.py` (end-to-end against the live room), loadfile-signature probe.
- mpv JSON IPC manual: https://mpv.io/manual/stable/#json-ipc
- `loadfile` / input commands: https://mpv.io/manual/stable/#list-of-input-commands
