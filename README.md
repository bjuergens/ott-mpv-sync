# ott-mpv-sync

Follow an [OpenTogetherTube](https://github.com/dyc3/opentogethertube) (OTT) room with
your local [mpv](https://mpv.io). `ott-mpv-sync` joins a room as a **passive follower** —
it mirrors the room's playback (source, play/pause, seek, speed) into mpv and **never**
sends commands back to the room. A small drift (~10 s) is fine by design.

It launches mpv for you and drives it over mpv's JSON IPC socket:

```
OTT room  ──wss──▶  ott-mpv-sync  ──unix socket / JSON IPC──▶  mpv
```

## Requirements

- **mpv** ≥ 0.37 on your `PATH` (or pass `--mpv /path/to/mpv`).
- Python ≥ 3.10 — though with `uvx` you don't install anything.
- Linux or macOS (mpv IPC uses a unix socket).

## Run it

With [uv](https://docs.astral.sh/uv/) — no install needed:

```sh
uvx ott-mpv-sync https://opentogethertube.com/room/your-room
```

Or run directly from github
```sh
uvx --from git+https://github.com/bjuergens/ott-mpv-sync ott-mpv-sync <room-url>
```

Or install it as a tool:

```sh
uv tool install ott-mpv-sync      # or: pipx install ott-mpv-sync
ott-mpv-sync https://opentogethertube.com/room/your-room
```

An mpv window opens and follows the room. Drive playback from the OTT web UI (or any
other member) — pause, seek, change source or speed — and mpv follows.

## Usage

```
ott-mpv-sync <room-url> [--mpv PATH] [--socket PATH] [-- <extra mpv args>]
```

- **`<room-url>`** (required) — the full room URL from your browser, e.g.
  `https://opentogethertube.com/room/2850ebf4-...`. The host, any base path, and the
  room name are derived from it; the tool builds `wss://host[/base]/api/room/<name>` and
  `https://host[/base]/api/auth/grant`. `http://` URLs use `ws://` (for local instances).
- **`--mpv PATH`** — mpv binary to launch (default: `mpv` on `PATH`).
- **`--socket PATH`** — mpv IPC socket path (default: an auto-generated temp path). Must
  not already exist.
- **`-- <extra mpv args>`** — anything after a literal `--` is passed through to mpv, e.g.
  `-- --fullscreen --volume=80`.

Examples:

```sh
ott-mpv-sync https://opentogethertube.com/room/movie-night
ott-mpv-sync https://ott.example.com/room/abc --mpv /usr/bin/mpv
ott-mpv-sync https://opentogethertube.com/room/movie-night -- --fullscreen
```

Stop by closing the mpv window or pressing Ctrl-C.

## Troubleshooting

The tool fails fast with a single `❌` line and a non-zero exit code when something is
wrong:

| Message | Cause / fix |
|---|---|
| `room URL must start with http(s)…` / `must contain a /room/<name> segment` | Paste the full room URL from your browser. |
| `mpv not found` | Install mpv, or pass `--mpv /path/to/mpv`. |
| `IPC socket already exists` | Remove the file or choose a different `--socket`. |
| `cannot reach OTT server` / `auth grant failed: HTTP …` | The host is unreachable or not an OTT server. |
| `could not join room …` | The room doesn't exist, or it's not reachable. |

`⚠️` lines are warnings (e.g. a dropped connection that is reconnecting, an old mpv).
A connection drop mid-session reconnects automatically and re-syncs from the room.

## Development

```sh
uv sync --group dev
uv run ruff check . && uv run ruff format --check .
uv run pytest
uv run ott-mpv-sync <room-url>          # run from source
```

- `src/ott_mpv_sync/` — package: `cli` (entry + validation), `roomurl` (URL → endpoints),
  `mpv` (launch + JSON IPC), `ott` (token + WebSocket), `media` (resolve a source/custom-
  media manifest → media url + subtitles), `follower` (sync → mpv commands).
- `tests/` — hermetic unit tests (URL parsing, delta translation). No network/mpv needed.
- `scripts/probes/` — manual integration probes against a live room / real mpv.
- `research/` — the OTT protocol reference and architecture notes that back the design.

## Limitations

Follow-only by design — it never controls the room. Source resolution targets `direct`
sources (`currentSource.src_url || id`): a plain media file (MP4, …) plays directly, and a
[custom media manifest](https://github.com/dyc3/opentogethertube/blob/master/docs/custom-media-format.md)
(a `.json` source) is fetched and unwrapped to its highest-quality source plus any subtitle
tracks it declares (attached via mpv `sub-files-append`). HLS/DASH are not yet wired up.
Locked/private rooms (which may need a logged-in token) are untested.

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE).
