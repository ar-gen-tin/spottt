"""Desktop widget — retro handheld shell with Spottt inside its LCD screen.

Runs a pywebview window (native macOS WebKit) showing the retro device,
with a background thread polling Spotify and feeding data via a local HTTP API.
"""

import os
import sys
import threading
import time

from spottt.auth import SpotifyAuth
from spottt.spotify import SpotifyClient
from spottt.renderer import AsciiRenderer
from spottt.desktop.server import state, set_action_callback, start_server


class SpotifyPoller:
    """Background poller that updates shared state."""

    def __init__(self, client_id: str):
        self.auth = SpotifyAuth(client_id)
        self.client = SpotifyClient(self.auth)
        self.renderer = AsciiRenderer()
        self.running = True
        self._fail_count = 0
        self.current_track = None
        self.current_image_bytes = None
        self.current_frame = None
        self.bpm = 120.0

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
                self._fail_count = 0
            except Exception as e:
                print(f"  Poll error: {e}", file=sys.stderr)
                self._fail_count += 1
                if self._fail_count >= 3:
                    state.set_error("Connection lost")
            time.sleep(2.0)

    def _poll(self):
        track = self.client.get_currently_playing()

        if track is None:
            self.current_track = None
            state.update_from_track(None, "", 120.0, self.renderer.current_style)
            return

        self.current_track = track

        # New track — fetch art
        if self.client.is_new_track(track):
            self.client.mark_track_seen(track)
            # Update metadata immediately before downloading art
            state.update_from_track(track, state.get_art_html(), self.bpm, self.renderer.current_style)
            self._fetch_art(track)
            self._fetch_bpm(track)

        # Update progress in state
        art_html = state.get_art_html()  # keep existing art
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
            art_html = self.current_frame.to_html(brightness=1.0)
            state.update_from_track(track, art_html, self.bpm, self.renderer.current_style)
        except Exception as e:
            print(f"  Art fetch failed: {e}", file=sys.stderr)

    def _fetch_bpm(self, track):
        try:
            features = self.client.get_audio_features(track.track_id)
            self.bpm = features.get("tempo", 120.0)
        except Exception as e:
            print(f"  BPM fetch failed: {e}", file=sys.stderr)
            self.bpm = 120.0

    def handle_action(self, action: str):
        # Playback controls
        if action == "play_pause":
            try:
                if self.current_track and self.current_track.is_playing:
                    self.client.pause()
                else:
                    self.client.play()
            except Exception as e:
                print(f"  play_pause failed: {e}", file=sys.stderr)
            return
        elif action == "next_track":
            try:
                self.client.next_track()
            except Exception as e:
                print(f"  next_track failed: {e}", file=sys.stderr)
            return
        elif action == "prev_track":
            try:
                self.client.previous_track()
            except Exception as e:
                print(f"  prev_track failed: {e}", file=sys.stderr)
            return

        elif action == "quit":
            import webview
            for w in webview.windows:
                w.destroy()
            return
        elif action == "minimize":
            import webview
            for w in webview.windows:
                w.minimize()
            return
        elif action == "fullscreen":
            import webview
            for w in webview.windows:
                w.toggle_fullscreen()
            return
        elif action == "shuffle":
            try:
                self.client.shuffle()
            except Exception as e:
                print(f"  shuffle failed: {e}", file=sys.stderr)
            return
        elif action == "repeat":
            try:
                self.client.set_repeat("context")
            except Exception as e:
                print(f"  repeat failed: {e}", file=sys.stderr)
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
                art_html = self.current_frame.to_html(brightness=1.0)
                state.update_from_track(
                    self.current_track, art_html, self.bpm,
                    self.renderer.current_style,
                )
            except Exception as e:
                print(f"  Style re-render failed: {e}", file=sys.stderr)


CONFIG_DIR = os.path.expanduser("~/.config/spottt")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def _load_client_id() -> str:
    """Load client ID from env, config file, or prompt user."""
    import json

    # 1. Environment variable
    cid = os.environ.get("SPOTIFY_CLIENT_ID") or os.environ.get("SPOTIPY_CLIENT_ID")
    if cid:
        return cid

    # 2. Config file
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            cid = data.get("client_id")
            if cid:
                return cid
        except Exception:
            pass

    # 3. GUI prompt via webview
    import webview

    result = [None]

    def _on_shown(window):
        # Use JS prompt via evaluate_js
        js = (
            'prompt('
            '"Enter your Spotify Client ID\\n\\n'
            'Get one at developer.spotify.com/dashboard\\n'
            'Set redirect URI to http://127.0.0.1:8888/callback",'
            '"")'
        )
        val = window.evaluate_js(js)
        if val and val.strip():
            result[0] = val.strip()
        window.destroy()

    w = webview.create_window(
        "Spottt — Setup",
        html="<html><body style='background:#141414;color:#c87000;font-family:monospace;"
             "display:flex;align-items:center;justify-content:center;height:100vh'>"
             "<p>Enter Spotify Client ID...</p></body></html>",
        width=400, height=150,
    )
    w.events.shown += _on_shown
    webview.start()

    cid = result[0]
    if not cid:
        print("No Client ID provided. Exiting.")
        sys.exit(1)

    # Save for next time
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    with open(CONFIG_FILE, "w") as f:
        json.dump({"client_id": cid}, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)

    return cid


def main(client_id: str = None):
    import webview

    # Hide Python rocket icon from Dock — run as accessory app
    try:
        import AppKit
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    except Exception:
        pass

    client_id = client_id or _load_client_id()

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
        width=840,
        height=340,
        resizable=True,
        frameless=True,
        transparent=True,
        on_top=False,
    )

    webview.start(debug=False)
    poller.running = False


if __name__ == "__main__":
    main()
