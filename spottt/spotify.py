"""Spotify Web API client — track info, images, polling."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TrackInfo:
    track_id: str
    name: str
    artists: list
    album: str
    album_images: list  # [{url, height, width}]
    artist_ids: list
    duration_ms: int
    progress_ms: int
    is_playing: bool
    timestamp: float = 0.0  # local time when this was fetched

    @property
    def artist_display(self) -> str:
        return ", ".join(self.artists)

    def interpolated_progress_ms(self) -> int:
        """Estimate current progress by adding elapsed time since last poll."""
        if not self.is_playing or self.timestamp == 0:
            return self.progress_ms
        elapsed = (time.time() - self.timestamp) * 1000
        return min(int(self.progress_ms + elapsed), self.duration_ms)

    @property
    def best_cover_url(self) -> Optional[str]:
        if not self.album_images:
            return None
        sorted_imgs = sorted(
            self.album_images,
            key=lambda x: abs((x.get("height") or 640) - 300),
        )
        return sorted_imgs[0]["url"]


class SpotifyClient:
    API_BASE = "https://api.spotify.com/v1"

    def __init__(self, auth):
        self.auth = auth
        self._last_track_id = None
        self._artist_image_cache = {}

    def _api_get(self, path: str, params: dict = None, _retries: int = 0):
        token = self.auth.get_token()
        url = f"{self.API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 204:
                    return None
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if _retries >= 2:
                return None
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", 5))
                time.sleep(retry_after)
                return self._api_get(path, params, _retries + 1)
            if e.code == 401:
                self.auth.access_token = None
                return self._api_get(path, params, _retries + 1)
            raise

    def get_currently_playing(self) -> Optional[TrackInfo]:
        data = self._api_get("/me/player/currently-playing")
        if not data or not data.get("item"):
            return None
        if data.get("currently_playing_type") != "track":
            return None

        item = data["item"]
        return TrackInfo(
            track_id=item["id"],
            name=item["name"],
            artists=[a["name"] for a in item.get("artists", [])],
            album=item.get("album", {}).get("name", ""),
            album_images=item.get("album", {}).get("images", []),
            artist_ids=[a["id"] for a in item.get("artists", [])],
            duration_ms=item.get("duration_ms", 0),
            progress_ms=data.get("progress_ms", 0),
            is_playing=data.get("is_playing", False),
            timestamp=time.time(),
        )

    def get_artist_image_url(self, artist_id: str) -> Optional[str]:
        if artist_id in self._artist_image_cache:
            return self._artist_image_cache[artist_id]

        data = self._api_get(f"/artists/{artist_id}")
        if not data or not data.get("images"):
            self._artist_image_cache[artist_id] = None
            return None

        sorted_imgs = sorted(
            data["images"],
            key=lambda x: abs((x.get("height") or 640) - 300),
        )
        url = sorted_imgs[0]["url"]
        self._artist_image_cache[artist_id] = url
        return url

    def get_audio_features(self, track_id: str) -> dict:
        """Get tempo/BPM for a track. Returns {} on failure."""
        try:
            data = self._api_get(f"/audio-features/{track_id}")
            return data or {}
        except Exception:
            return {}

    def get_audio_analysis(self, track_id: str) -> dict:
        """Get detailed beat/segment data. Returns {} on failure."""
        try:
            data = self._api_get(f"/audio-analysis/{track_id}")
            return data or {}
        except Exception:
            return {}

    # ── Playback controls ────────────────────────────────────────

    def _api_request(self, method: str, path: str, body: dict = None, _retries: int = 0):
        token = self.auth.get_token()
        url = f"{self.API_BASE}{path}"
        data = json.dumps(body).encode() if body else b""
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            if _retries >= 2:
                return e.code
            if e.code == 401:
                self.auth.access_token = None
                return self._api_request(method, path, body, _retries + 1)
            return e.code

    def play(self):
        return self._api_request("PUT", "/me/player/play")

    def pause(self):
        return self._api_request("PUT", "/me/player/pause")

    def next_track(self):
        return self._api_request("POST", "/me/player/next")

    def previous_track(self):
        return self._api_request("POST", "/me/player/previous")

    def shuffle(self, state: bool = True):
        return self._api_request("PUT", f"/me/player/shuffle?state={'true' if state else 'false'}")

    def set_repeat(self, mode: str = "off"):
        return self._api_request("PUT", f"/me/player/repeat?state={mode}")

    def download_image(self, url: str) -> bytes:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    def track_changed(self, track: TrackInfo) -> bool:
        changed = track.track_id != self._last_track_id
        self._last_track_id = track.track_id
        return changed
