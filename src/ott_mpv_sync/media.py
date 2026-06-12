"""Resolve an OTT `currentSource` into something mpv can actually play.

OTT's `direct` service points at either a real media file (mp4, mkv, …) or a
*custom media manifest* — a small JSON document that describes the real
source(s) and any subtitle tracks, rather than being playable itself (see
opentogethertube `docs/custom-media-format.md`). mpv chokes on the manifest
(`Failed to recognize file format`), so when we spot one we fetch it and pull
out the bits mpv needs: the best video source URL and every text track.

A normal direct source passes straight through unchanged.
"""

import json
import urllib.error
import urllib.request

from .utils import warn

_FETCH_TIMEOUT = 10.0


class ResolvedSource:
    """A media URL plus the subtitle tracks to attach when loading it."""

    __slots__ = ("url", "subtitles")

    def __init__(self, url: str, subtitles: list[dict]):
        self.url = url
        self.subtitles = subtitles  # each: {"url", "title", "lang", "default"}


def resolve(cs: dict) -> ResolvedSource:
    """Map an OTT currentSource onto a playable URL (+ subtitle tracks).

    A real media file is its own URL with no subtitles. A custom-media manifest
    (mime `application/json`, or a `.json` URL) is fetched and unwrapped to the
    highest-quality source and its declared text tracks. On any fetch/parse
    error we fall back to the manifest URL — no worse than before this existed.
    """
    url = cs.get("src_url") or cs["id"]
    if not _is_manifest(cs, url):
        return ResolvedSource(url, [])
    try:
        manifest = _fetch_json(url)
        resolved = _from_manifest(manifest)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError, KeyError) as e:
        warn(f"could not resolve custom media manifest {url}: {e}; loading it directly")
        return ResolvedSource(url, [])
    return resolved


def _is_manifest(cs: dict, url: str) -> bool:
    if cs.get("mime") == "application/json":
        return True
    return url.split("?", 1)[0].lower().endswith(".json")


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as r:
        return json.load(r)


def _from_manifest(manifest: dict) -> ResolvedSource:
    """Pick the best source and collect subtitle tracks from a parsed manifest."""
    sources = manifest.get("sources") or []
    if not sources:
        raise ValueError("manifest has no sources")
    # Highest quality wins; mpv handles the rest of the muxing/codec details.
    best = max(sources, key=lambda s: s.get("quality") or 0)
    url = best["url"]

    subs = []
    for t in manifest.get("textTracks") or []:
        if not t.get("url"):
            continue
        subs.append(
            {
                "url": t["url"],
                "title": t.get("name") or "",
                "lang": t.get("srclang") or "",
                "default": bool(t.get("default")),
            }
        )
    # mpv auto-selects the first external sub it's given, so put any track the
    # manifest marks `default` first.
    subs.sort(key=lambda s: not s["default"])
    return ResolvedSource(url, subs)
