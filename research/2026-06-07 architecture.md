# WebSocket layer architecture: options & recommendation

**Question:** the follower must hold a persistent `wss://` connection to an OTT room
(see `2026-06-07 initial.md`) and translate `sync` frames into mpv playback commands.
mpv's embedded script runtimes **cannot open a TLS WebSocket**. Where, then, does the
WebSocket live, and how does it reach mpv?

**Method:** firsthand capability probes against the installed **mpv 0.37.0 / curl 8.5.0**
(✅ = I ran it), plus web verification of the curl WS status and a real-world precedent
(🌐). Items I could not fully verify are flagged.

---

## 1. The hard constraint (verified)

Every "do the WebSocket *inside* mpv" path is dead on arrival:

| Capability | Result | How verified |
|---|---|---|
| mpv Lua version | **PUC Lua 5.2**, not LuaJIT | ✅ `_VERSION="Lua 5.2"` |
| LuaJIT `ffi` (for OpenSSL/libwebsockets binding) | **absent** (`jit=false`, `require("ffi")`→false) | ✅ probe |
| `luasocket` / `luasec` (TCP + TLS) | **not bundled** (`require("socket")`→false) | ✅ probe |
| mpv JS (mujs) scripting | **loads**, but mujs is ES5 with **no networking APIs** | ✅ `.js` prints, no `fetch`/sockets |
| `curl` CLI WebSocket relay | **no WS in this build**; upstream CLI WS *"has not been started"*, frames capped 64K, stdin-send unimplemented — **libcurl-only** | ✅ `curl ws://`→"Protocol not supported" · 🌐 curl.se/docs/websocket.html |
| `subprocess` `capture_stdout` streaming | **exit-only** — *"Capture all data … and return it once the process ends."* No per-line callback. | ✅ subagent man-page read + empirical: callback fired once at process exit, not per line |

Two consequences that kill otherwise-tempting designs:

- **No in-mpv WebSocket** (Lua/JS/FFI). Even where another mpv build *does* link LuaJIT,
  it means hand-rolling TLS + WS framing against OpenSSL via FFI — fragile, platform-specific,
  security-sensitive, and **not portable** (this build proves you can't assume `ffi`).
- **`subprocess` stdout is exit-only**, so the "spawn a helper, read its stdout live" pattern
  *cannot stream events* — output is invisible until the helper dies. This is the trap to avoid.

**Therefore the TLS WebSocket must run in a separate, long-lived process.** The only real
design choice is the **boundary**: how that process talks to mpv, and who launches whom.

---

## 2. Transports from an external helper → mpv

Two viable channels (verified):

**(T1) mpv JSON IPC** — `--input-ipc-server=<path>` (or `set_property("input-ipc-server",…)`).
A unix socket (named pipe on Windows) speaking newline-delimited JSON:
`{"command":["loadfile","<url>"]}`, `{"command":["set_property","pause",true]}`,
`{"command":["seek","<abs>","absolute"]}`. **Bidirectional**: replies *and* async events,
including `observe_property` → `property-change` (so the helper can read `time-pos` to apply
the §6 seek-threshold check). **No auth, no encryption** — local-only by design (filesystem
perms on the socket). 🌐 mpv.io JSON IPC manual.

**(T2) File-poll** — helper appends newline-JSON to a regular file; a Lua script polls it on
`mp.add_periodic_timer` and applies commands. **One-way** (helper→mpv only). Gotchas (both
verified by subagent): a **FIFO + blocking `read` freezes mpv's event loop**, and a held-open
regular file hits **sticky EOF** in Lua 5.2. The safe pattern is *reopen + `seek(lastOffset)` +
read new lines + save offset* each tick. Strictly weaker than IPC (one-way, poll latency) for
the same script-UX.

---

## 3. Options

### ⭐ Option A — Lua bootstrap + helper over IPC (the *mpv-discord* pattern)
A tiny mpv script enables the IPC server and spawns the long-lived helper; the helper holds the
OTT `wss` and drives mpv over IPC. **This is exactly how `tnychn/mpv-discord` works** 🌐 — its
`discord.lua` does, verbatim:

```lua
mp.set_property("input-ipc-server", socket_path)          -- per-PID socket
mp.command_native_async({
  name = "subprocess", playback_only = false,             -- outlives playback
  args = { options.binary_path, socket_path, ... },       -- pass socket to helper
}, function() end)
```

Its Go helper connects to that IPC socket to read mpv state *and* maintains a persistent
external connection (Discord IPC). Our case is the same shape — swap "Discord IPC" for "OTT wss".

- **Pros:** native *"install an mpv script"* UX; battle-tested precedent; all real logic lives in
  a language with a proper WS+TLS stack (Lua is a ~10-line bootstrap); IPC is bidirectional so the
  exit-only-stdout problem never arises and the helper can read `time-pos`; composes with the
  user's normal mpv.
- **Cons:** must ship/build a helper (a static Go/Rust binary, or depend on a Python/Node runtime);
  IPC socket is unauthenticated (acceptable — local only).

### Option B — Helper-as-launcher + IPC (no mpv script)
A launcher (`ottsync-follow <room>`) starts `mpv --input-ipc-server=…`, connects to OTT, drives
mpv. No Lua at all. **Pros:** one language, simplest model. **Cons:** not a "plugin" UX — the user
runs *our* command instead of mpv; we own mpv's lifecycle/args; awkward to combine with the user's
own mpv config/invocation. This is the "companion process" idea, inverted ownership.

### Option C — Lua script + helper over file-poll (T2)
Script spawns helper; helper appends normalized sync deltas to a temp file; Lua polls and applies,
owning the §6 state machine. **Pros:** native script UX, no IPC socket. **Cons:** strictly worse
than A — one-way, poll-latency, reopen+seek hack — for the *same* UX A gets cleanly over IPC. Only
preferable if IPC is unavailable (it isn't).

### Dead options (do not pursue)
- **In-mpv pure-Lua / FFI wss** — impossible here (no `ffi`); non-portable and hand-rolled TLS
  elsewhere.
- **mujs/JS-script networking** — no networking APIs.
- **`curl`/`websocat` CLI as a wss pipe feeding Lua stdout** — curl CLI WS isn't usable, and even a
  working WS CLI can't stream into Lua because `subprocess` stdout is **exit-only**.

---

## 4. Helper language

| | Build/deps | Distribution | Notes |
|---|---|---|---|
| **Go / Rust** | compile per-platform static binary | **zero runtime deps** (mpv-discord ships Go binaries) | best for end-user distribution |
| **Python** (`websockets`) | none to write | needs Python + `websockets` at runtime | **we already have a working `wss` client** in `test/probe-ott.py` |
| **Node** (`ws`) | none to write | needs Node at runtime | fine; no existing code |

---

## 5. Recommendation

**Adopt Option A (Lua bootstrap + helper over IPC).** Prototype the helper in **Python**, reusing
the validated `test/probe-ott.py` connection code — fastest path to a working follower — then keep
the option to recompile to a **Go/Rust** static binary for dependency-free distribution (the
mpv-discord model). Rationale:

1. The TLS WebSocket *must* be external (Section 1) — non-negotiable.
2. Among external designs, IPC (T1) dominates file-poll (T2): bidirectional, lower latency, lets the
   helper read `time-pos` for the §6 seek threshold, and sidesteps the exit-only-stdout trap.
3. The Lua-bootstrap entry (A over B) preserves the "it's an mpv plugin" UX the project is built
   around, and has a proven precedent doing precisely this.

Concretely: `scripts/ottsync/main.lua` sets `input-ipc-server` to a per-PID socket and
`command_native_async`-spawns the helper (`playback_only=false`) with the socket path + room id; the
helper runs the §6 follower loop, issuing `loadfile` / `set pause` / `seek` / `set speed` over IPC.

### To verify before committing to the build
- **Locked/private rooms** still untested (Q2 in `initial.md`) — the helper auth path may need a
  logged-in session token.
- **Exact `subprocess` online wording** — verified firsthand from the local man page and empirically;
  the mpv.io anchor didn't resolve via fetch. Re-quote from mpv.io/manual if a citation is needed.
- **Windows** — IPC becomes a named pipe; Python/Go clients must use the pipe API (socat won't work).
  Out of scope unless we target Windows.

---

## Sources
- mpv-discord (precedent — IPC + spawned helper): https://github.com/tnychn/mpv-discord ·
  `scripts/discord.lua` https://github.com/tnychn/mpv-discord/blob/main/scripts/discord.lua
- curl WebSocket status (CLI relay "not started", libcurl-only): https://curl.se/docs/websocket.html
- mpv JSON IPC (bidirectional, no auth): https://mpv.io/manual/stable/#json-ipc
- mpv `subprocess` command (`capture_stdout` returns once the process ends): https://mpv.io/manual/stable/#list-of-input-commands
- Firsthand probes: mpv 0.37.0 (`_VERSION`/`jit`/`ffi`/`socket`/JS), curl 8.5.0 (`ws://` unsupported),
  subprocess exit-only stdout, FIFO-blocking freeze, reopen+seek file-poll — this machine, 2026-06-07.
