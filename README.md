# mpv-opentogethersync

An mpv plugin that joins an [OpenTogetherTube](https://github.com/dyc3/opentogethertube)
(OTT) room as a **passive follower** — it mirrors the room's playback (source,
play/pause, seek, speed) and never issues commands back to the room.

The protocol research backing the eventual implementation lives in
[`research/`](research/).

## Status

**Phase 1 — scaffolding (current).** The plugin is a dummy that proves the mpv
script harness works: it loads, runs on mpv's event loop, reads player state,
and logs a heartbeat with a timestamp roughly once per second. No networking or
playback control yet.

Next phases wire up the OTT WebSocket connection and translate room `sync`
frames into mpv commands (`loadfile`, `seek`, `set pause`, `set speed`).

## Layout

```
scripts/ottsync/main.lua   the mpv plugin (Lua; mpv scripting is LuaJIT)
test/run-dummy.sh          smoke test — runs mpv with the script, checks heartbeats
research/                  OTT protocol reference
```

## Trying it

The script is a directory-based mpv script (`scripts/ottsync/`), so it can grow
into multiple modules.

Run the smoke test (no install needed):

```sh
test/run-dummy.sh                 # mpv idle, ~4s, expects heartbeats
test/run-dummy.sh path/to/clip.mp4   # play a file, watch time-pos advance
DURATION=8 test/run-dummy.sh      # run longer
```

Load it manually in your own mpv:

```sh
mpv --script=scripts/ottsync --msg-level=ottsync=info <file>
```

Install into your mpv config (symlink so edits take effect immediately):

```sh
ln -s "$(pwd)/scripts/ottsync" ~/.config/mpv/scripts/ottsync
```

Then watch the heartbeats in mpv's terminal output, or in the log if you run
mpv with `--log-file`.
