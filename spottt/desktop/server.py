"""Lightweight HTTP API server that feeds Spottt data to the desktop shell."""

import json
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 18899
ALLOWED_ORIGIN = "http://127.0.0.1"
ACTION_THROTTLE_MS = 500

ALLOWED_ACTIONS = {"play_pause", "next_track", "prev_track", "next_style", "prev_style", "quit", "minimize", "fullscreen", "shuffle", "repeat"}

# Per-action throttle timestamps
_action_last_time = {}


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
        self.error = ""
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
                "error": self.error,
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
                self.error = ""
            else:
                self.track_id = None
                self.name = ""
                self.artist = ""
                self.album = ""
                self.progress_ms = 0
                self.duration_ms = 0
                self.is_playing = False
                self.art_html = ""

    def set_error(self, msg):
        with self._lock:
            self.error = msg


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
            if action not in ALLOWED_ACTIONS:
                self.send_error(400, "Invalid action")
                return
            # Throttle: ignore repeated same-action within 500ms
            now = time.monotonic() * 1000
            last = _action_last_time.get(action, 0)
            if now - last < ACTION_THROTTLE_MS:
                self._json_response({"ok": True, "throttled": True})
                return
            _action_last_time[action] = now
            if _action_callback:
                _action_callback(action)
            self._json_response({"ok": True})
        else:
            self.send_error(404)

    def _json_response(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # Silence logs


def start_server():
    """Start the API server in a background thread. Tries ports 18899-18901."""
    for port in [PORT, PORT + 1, PORT + 2]:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), APIHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            if port != PORT:
                print(f"  API server started on port {port} (default {PORT} was busy)",
                      file=__import__('sys').stderr)
            return server
        except OSError:
            continue
    raise RuntimeError(
        f"Could not bind API server on ports {PORT}-{PORT + 2}. "
        "Is another Spottt instance running?"
    )
