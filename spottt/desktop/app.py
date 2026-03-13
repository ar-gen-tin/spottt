"""Desktop widget — retro handheld shell with Spottt inside its LCD screen.

Runs a pywebview window (native macOS WebKit) showing the retro device,
with a background thread polling Spotify and feeding data via a local HTTP API.
"""

import html
import os
import sys
import threading
import time

# Ensure parent package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from spottt.auth import SpotifyAuth
from spottt.spotify import SpotifyClient
from spottt.renderer import AsciiRenderer
from spottt.desktop.server import state, set_action_callback, start_server


def ansi_to_html(ansi_str: str) -> str:
    """Convert ANSI-colored ASCII art string to HTML spans."""
    import re
    if not ansi_str:
        return ""

    result = []
    lines = ansi_str.split("\n")

    for line in lines:
        parts = re.split(r'(\033\[[0-9;]*m)', line)
        html_line = []
        current_color = None

        for part in parts:
            if part.startswith('\033['):
                # Parse ANSI escape
                codes = part[2:-1]  # strip \033[ and m
                if codes == '0':
                    if current_color:
                        html_line.append('</span>')
                        current_color = None
                elif codes.startswith('38;2;'):
                    # True color: 38;2;R;G;B
                    rgb = codes[5:].split(';')
                    if len(rgb) >= 3:
                        if current_color:
                            html_line.append('</span>')
                        color = f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"
                        html_line.append(f'<span style="color:{color}">')
                        current_color = color
            else:
                html_line.append(html.escape(part))

        if current_color:
            html_line.append('</span>')

        result.append(''.join(html_line))

    return '\n'.join(result)


class SpotifyPoller:
    """Background poller that updates shared state."""

    def __init__(self, client_id: str):
        self.auth = SpotifyAuth(client_id)
        self.client = SpotifyClient(self.auth)
        self.renderer = AsciiRenderer()
        self.running = True
        self.current_track = None
        self.current_image_bytes = None
        self.current_frame = None
        self.bpm = 120.0
        self._last_track_id = None

    def run(self):
        """Main poll loop — runs in a background thread."""
        # Initial auth
        try:
            self.auth.get_token()
        except Exception as e:
            print(f"Auth failed: {e}")
            return

        while self.running:
            try:
                self._poll()
            except Exception:
                pass
            time.sleep(2.0)

    def _poll(self):
        track = self.client.get_currently_playing()

        if track is None:
            self.current_track = None
            state.update_from_track(None, "", 120.0, self.renderer.current_style)
            return

        self.current_track = track

        # New track — fetch art
        if self.client.track_changed(track):
            self._fetch_art(track)
            self._fetch_bpm(track)

        # Update progress in state
        art_html = state.get_art_html()  # keep existing art
        if self.current_frame:
            # Re-render is not needed every poll, art_html persists
            pass

        state.update_from_track(track, art_html, self.bpm, self.renderer.current_style)

    def _fetch_art(self, track):
        cover_url = track.best_cover_url
        if not cover_url and track.artist_ids:
            cover_url = self.client.get_artist_image_url(track.artist_ids[0])

        if not cover_url:
            state.update_from_track(track, "", self.bpm, self.renderer.current_style)
            return

        try:
            self.current_image_bytes = self.client.download_image(cover_url)
            self.current_frame = self.renderer.render_frame(
                self.current_image_bytes, cols=65, track_id=track.track_id,
            )
            ansi = self.renderer.render_with_pulse(self.current_frame, 1.0)
            art_html = ansi_to_html(ansi)
            state.update_from_track(track, art_html, self.bpm, self.renderer.current_style)
        except Exception:
            pass

    def _fetch_bpm(self, track):
        try:
            features = self.client.get_audio_features(track.track_id)
            self.bpm = features.get("tempo", 120.0)
        except Exception:
            self.bpm = 120.0

    def handle_action(self, action: str):
        # Playback controls
        if action == "play_pause":
            try:
                if self.current_track and self.current_track.is_playing:
                    self.client.pause()
                else:
                    self.client.play()
            except Exception:
                pass
            return
        elif action == "next_track":
            try:
                self.client.next_track()
            except Exception:
                pass
            return
        elif action == "prev_track":
            try:
                self.client.previous_track()
            except Exception:
                pass
            return

        # Style controls
        if action == "next_style":
            self.renderer.next_style()
        elif action == "prev_style":
            self.renderer.prev_style()

        # Re-render with new style
        if self.current_image_bytes and self.current_track:
            self.renderer.clear_cache()
            try:
                self.current_frame = self.renderer.render_frame(
                    self.current_image_bytes, cols=65,
                    track_id=self.current_track.track_id,
                )
                ansi = self.renderer.render_with_pulse(self.current_frame, 1.0)
                art_html = ansi_to_html(ansi)
                state.update_from_track(
                    self.current_track, art_html, self.bpm,
                    self.renderer.current_style,
                )
            except Exception:
                pass


def main(client_id: str = None):
    import webview

    client_id = (
        client_id
        or os.environ.get("SPOTIFY_CLIENT_ID")
        or os.environ.get("SPOTIPY_CLIENT_ID")
    )
    if not client_id:
        print("Error: Set SPOTIFY_CLIENT_ID environment variable")
        sys.exit(1)

    # Start API server
    start_server()

    # Start Spotify poller
    poller = SpotifyPoller(client_id)
    set_action_callback(poller.handle_action)
    poll_thread = threading.Thread(target=poller.run, daemon=True)
    poll_thread.start()

    # Open the retro shell in a native window
    html_path = os.path.join(os.path.dirname(__file__), "shell.html")

    window = webview.create_window(
        "Spottt",
        url=f"file://{html_path}",
        width=310,
        height=340,
        resizable=False,
        frameless=True,
        transparent=True,
        on_top=True,
    )

    webview.start(debug=False)
    poller.running = False


if __name__ == "__main__":
    main()
