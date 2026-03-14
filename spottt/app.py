"""Main application loop — ties together Spotify polling, ASCII rendering, rhythm, and TUI."""

import os
import select
import signal
import sys
import termios
import threading
import time
import tty

from .auth import SpotifyAuth
from .spotify import SpotifyClient
from .renderer import AsciiRenderer
from .rhythm import RhythmEngine
from .ui import SpotttUI, LOGO_LINES, enter_alt_screen, leave_alt_screen, clear_screen


class SpotttApp:
    def __init__(self, client_id: str = None, cols: int = 0, style: str = None):
        self.client_id = (
            client_id
            or os.environ.get("SPOTIFY_CLIENT_ID")
            or os.environ.get("SPOTIPY_CLIENT_ID")
        )
        if not self.client_id:
            print(
                "\n  Error: Spotify Client ID required.\n"
                "\n  Set SPOTIFY_CLIENT_ID environment variable or pass --client-id.\n"
                "  Create one at: https://developer.spotify.com/dashboard\n"
                "\n  Steps:\n"
                "  1. Go to Spotify Developer Dashboard\n"
                "  2. Create an app (set redirect URI to http://127.0.0.1:8888/callback)\n"
                "  3. Copy the Client ID\n"
                '  4. Run: export SPOTIFY_CLIENT_ID="your_id_here"\n'
            )
            sys.exit(1)

        self.auth = SpotifyAuth(self.client_id)
        self.client = SpotifyClient(self.auth)
        self.renderer = AsciiRenderer()
        self.rhythm = RhythmEngine()
        self.ui = SpotttUI()

        self.user_cols = cols  # 0 = auto
        self.current_track = None
        self.current_art = None       # ANSI string (regenerated each frame with pulse)
        self.current_frame = None     # ArtFrame (raw chars+colors, cached per track)
        self.current_image_bytes = None
        self.running = True
        self.poll_interval = 3.0
        self.last_poll = 0.0

        if style:
            self.renderer.set_style(style)

    def _art_cols(self) -> int:
        if self.user_cols > 0:
            return self.user_cols
        w = self.ui.width
        return max(20, min(70, w - 10))

    def run(self):
        old_settings = termios.tcgetattr(sys.stdin)

        try:
            tty.setcbreak(sys.stdin.fileno())
            enter_alt_screen()

            signal.signal(signal.SIGINT, lambda *_: self._quit())
            signal.signal(signal.SIGWINCH, lambda *_: self._on_resize())

            # Auth — show splash in alt screen
            clear_screen()
            for line in LOGO_LINES:
                sys.stdout.write(f"  \033[38;2;30;215;96m{line}\033[0m\n")
            sys.stdout.write("\n  Connecting to Spotify...\n")
            sys.stdout.flush()
            self.auth.get_token()
            sys.stdout.write("  Connected! Starting...\n")
            sys.stdout.flush()
            time.sleep(0.3)

            while self.running:
                self._handle_input()

                now = time.time()
                if now - self.last_poll >= self.poll_interval:
                    self._poll_spotify()
                    self.last_poll = now

                # Update rhythm + regenerate art with pulse every frame
                if self.current_track:
                    progress = self.current_track.interpolated_progress_ms()
                    self.rhythm.update(progress, self.current_track.is_playing)

                    # Apply beat pulse to album cover colors
                    if self.current_frame:
                        self.current_art = self.renderer.render_with_pulse(
                            self.current_frame, self.rhythm.pulse
                        )

                self.ui.render(
                    self.current_track,
                    self.current_art,
                    self.renderer.current_style,
                    self.current_track.is_playing if self.current_track else False,
                    rhythm=self.rhythm if self.current_track else None,
                )

                time.sleep(0.08)  # ~12 FPS for smooth rhythm animation

        except Exception as e:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            leave_alt_screen()
            print(f"\n  Error: {e}\n")
            raise
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            leave_alt_screen()

    def _handle_input(self):
        if not select.select([sys.stdin], [], [], 0)[0]:
            return

        ch = sys.stdin.read(1)

        if ch in ("q", "Q"):
            self._quit()
        elif ch == "s":
            self.renderer.next_style()
            self._re_render_art()
        elif ch == "S":
            self.renderer.prev_style()
            self._re_render_art()
        elif ch == "+":
            if self.user_cols == 0:
                self.user_cols = self._art_cols()
            self.user_cols = min(120, self.user_cols + 5)
            self.renderer.clear_cache()
            self._re_render_art()
        elif ch == "-":
            if self.user_cols == 0:
                self.user_cols = self._art_cols()
            self.user_cols = max(15, self.user_cols - 5)
            self.renderer.clear_cache()
            self._re_render_art()
        elif ch == "0":
            self.user_cols = 0
            self.renderer.clear_cache()
            self._re_render_art()

    def _poll_spotify(self):
        try:
            track = self.client.get_currently_playing()

            if track is None:
                self.current_track = None
                self.current_art = None
                self.current_frame = None
                self.current_image_bytes = None
                self.poll_interval = 8.0
                return

            if self.client.track_changed(track):
                self.current_track = track
                self._fetch_and_render_art(track)
                # Setup rhythm for new track
                self._load_rhythm_data(track)
                self.poll_interval = 3.0
            else:
                self.current_track = track
                remaining = track.duration_ms - track.progress_ms
                if remaining < 10000:
                    self.poll_interval = 1.0
                elif not track.is_playing:
                    self.poll_interval = 10.0
                else:
                    self.poll_interval = 3.0

        except Exception:
            self.poll_interval = 5.0

    def _load_rhythm_data(self, track):
        """Load audio analysis for rhythm sync. Runs in background thread."""
        self.rhythm.set_track(track.track_id)

        def _fetch():
            try:
                # Try audio features first for BPM
                features = self.client.get_audio_features(track.track_id)
                tempo = features.get("tempo", 120.0)
                self.rhythm.set_track(track.track_id, bpm=tempo)

                # Try full audio analysis for beats/segments
                analysis = self.client.get_audio_analysis(track.track_id)
                if analysis:
                    beats = analysis.get("beats", [])
                    segments = analysis.get("segments", [])
                    analysis_tempo = analysis.get("track", {}).get("tempo", tempo)
                    self.rhythm.set_audio_analysis(beats, segments, analysis_tempo)
            except Exception:
                pass  # Fall back to simulated rhythm

        # Run in background so it doesn't block rendering
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()

    def _fetch_and_render_art(self, track):
        cols = self._art_cols()

        cover_url = track.best_cover_url

        if not cover_url and track.artist_ids:
            cover_url = self.client.get_artist_image_url(track.artist_ids[0])

        if not cover_url:
            self.current_art = None
            self.current_frame = None
            self.current_image_bytes = None
            return

        try:
            self.current_image_bytes = self.client.download_image(cover_url)
            self.current_frame = self.renderer.render_frame(
                self.current_image_bytes,
                cols=cols,
                track_id=track.track_id,
            )
            self.current_art = self.renderer.render_with_pulse(self.current_frame, 1.0)
        except Exception:
            self.current_art = None
            self.current_frame = None

    def _re_render_art(self):
        if not self.current_image_bytes or not self.current_track:
            return
        cols = self._art_cols()
        try:
            self.current_frame = self.renderer.render_frame(
                self.current_image_bytes,
                cols=cols,
                track_id=self.current_track.track_id,
            )
            self.current_art = self.renderer.render_with_pulse(self.current_frame, 1.0)
        except Exception:
            pass

    def _on_resize(self):
        self.ui.width, self.ui.height = os.get_terminal_size()
        self.ui.invalidate()  # full clear to wipe resize artifacts
        if self.user_cols == 0:
            self.renderer.clear_cache()
            self._re_render_art()

    def _quit(self):
        self.running = False
