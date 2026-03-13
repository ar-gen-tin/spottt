"""Lightweight HTTP API server that feeds Spottt data to the desktop shell."""

import html
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 18899


class SpotttState:
    """Shared state between the Spotify poller and the HTTP API."""

    def __init__(self):
        self.track_id = None
        self.name = ""
        self.artist = ""
        self.album = ""
        self.progress_ms = 0
        self.duration_ms = 0
        self.is_playing = False
        self.bpm = 120.0
        self.art_html = ""  # pre-rendered HTML for the ASCII art
        self.style = "braille"
        self._lock = threading.Lock()

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "track_id": self.track_id,
                "name": self.name,
                "artist": self.artist,
                "album": self.album,
                "progress_ms": self.progress_ms,
                "duration_ms": self.duration_ms,
                "is_playing": self.is_playing,
                "bpm": self.bpm,
                "style": self.style,
            }

    def get_art_html(self) -> str:
        with self._lock:
            return self.art_html

    def update_from_track(self, track, art_html: str, bpm: float, style: str):
        with self._lock:
            if track:
                self.track_id = track.track_id
                self.name = track.name
                self.artist = track.artist_display
                self.album = track.album
                self.progress_ms = track.interpolated_progress_ms()
                self.duration_ms = track.duration_ms
                self.is_playing = track.is_playing
                self.art_html = art_html
                self.bpm = bpm
                self.style = style
            else:
                self.track_id = None
                self.name = ""
                self.artist = ""
                self.album = ""
                self.progress_ms = 0
                self.duration_ms = 0
                self.is_playing = False
                self.art_html = ""


# Global shared state
state = SpotttState()

# Callback for button actions
_action_callback = None


def set_action_callback(cb):
    global _action_callback
    _action_callback = cb


class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/state":
            self._json_response(state.to_dict())
        elif self.path == "/art":
            self._json_response({"html": state.get_art_html()})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/action/"):
            action = self.path.split("/action/")[1]
            if _action_callback:
                _action_callback(action)
            self._json_response({"ok": True})
        else:
            self.send_error(404)

    def _json_response(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # Silence logs


def start_server():
    """Start the API server in a background thread."""
    server = HTTPServer(("127.0.0.1", PORT), APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
