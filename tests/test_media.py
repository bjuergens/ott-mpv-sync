"""Custom-media-manifest resolution (ott_mpv_sync.media). No network: the JSON
fetch is monkeypatched so these stay hermetic like the rest of the suite."""

from ott_mpv_sync import media

MANIFEST = {
    "title": "JASSUB Demo",
    "duration": 90.01,
    "sources": [
        {"url": "https://x/lo.mp4", "contentType": "video/mp4", "quality": 480},
        {"url": "https://x/hi.mp4", "contentType": "video/mp4", "quality": 1080},
    ],
    "textTracks": [
        {"url": "https://x/en.ass", "contentType": "text/x-ass", "srclang": "en"},
        {"url": "https://x/de.vtt", "contentType": "text/vtt", "srclang": "de", "default": True},
    ],
}


def test_direct_media_passes_through():
    r = media.resolve({"service": "direct", "id": "https://x/v.mp4", "mime": "video/mp4"})
    assert r.url == "https://x/v.mp4"
    assert r.subtitles == []


def test_src_url_preferred_over_id():
    r = media.resolve({"service": "direct", "src_url": "https://x/a.mp4", "id": "https://x/b.mp4"})
    assert r.url == "https://x/a.mp4"


def test_manifest_by_mime_picks_best_source_and_subs(monkeypatch):
    monkeypatch.setattr(media, "_fetch_json", lambda url: MANIFEST)
    r = media.resolve({"service": "direct", "id": "https://x/m.json", "mime": "application/json"})
    assert r.url == "https://x/hi.mp4"  # highest quality
    assert [s["url"] for s in r.subtitles] == [
        "https://x/de.vtt",
        "https://x/en.ass",
    ]  # default first
    assert r.subtitles[0]["default"] is True
    assert r.subtitles[0]["lang"] == "de"


def test_manifest_detected_by_json_extension(monkeypatch):
    monkeypatch.setattr(media, "_fetch_json", lambda url: MANIFEST)
    r = media.resolve({"service": "direct", "id": "https://x/m.json?token=1"})
    assert r.url == "https://x/hi.mp4"


def test_manifest_fetch_failure_falls_back_to_url(monkeypatch):
    def boom(url):
        raise OSError("network down")

    monkeypatch.setattr(media, "_fetch_json", boom)
    r = media.resolve({"service": "direct", "id": "https://x/m.json", "mime": "application/json"})
    assert r.url == "https://x/m.json"
    assert r.subtitles == []


def test_manifest_without_sources_falls_back(monkeypatch):
    monkeypatch.setattr(media, "_fetch_json", lambda url: {"title": "x", "sources": []})
    r = media.resolve({"service": "direct", "id": "https://x/m.json", "mime": "application/json"})
    assert r.url == "https://x/m.json"
