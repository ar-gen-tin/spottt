"""Terminal UI — cliamp-inspired retro layout with Spotify green accent and rhythm."""

import math
import os
import re
import shutil
import sys

# ── ANSI color palette ───────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"

# Spotify brand green
GREEN = "\033[38;2;30;215;96m"
GREEN_BG = "\033[48;2;30;215;96m"
# Complementary accent
CYAN = "\033[38;2;0;210;210m"
MAGENTA = "\033[38;2;180;80;220m"
# Neutrals
WHITE = "\033[38;2;255;255;255m"
GRAY = "\033[38;2;120;120;120m"
DARK = "\033[38;2;60;60;60m"
DIMWHITE = "\033[38;2;180;180;180m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# Spectrum bar block characters (9 levels: empty → full)
_BAR_CHARS = " ▁▂▃▄▅▆▇█"


def _rgb(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _pad_center(text: str, width: int) -> str:
    vlen = _visible_len(text)
    if vlen >= width:
        return text
    pad = (width - vlen) // 2
    return " " * pad + text


def _pad_right(text: str, width: int) -> str:
    vlen = _visible_len(text)
    if vlen >= width:
        return text
    return text + " " * (width - vlen)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


# ── Screen control ───────────────────────────────────────────────────────────

def enter_alt_screen():
    """Enter alternate screen buffer (like cliamp's tea.WithAltScreen)."""
    sys.stdout.write("\033[?1049h\033[2J\033[H\033[?25l")
    sys.stdout.flush()


def leave_alt_screen():
    """Leave alternate screen buffer and restore cursor."""
    sys.stdout.write("\033[?25h\033[?1049l")
    sys.stdout.flush()


def clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


# ── UI renderer ──────────────────────────────────────────────────────────────

LOGO_LINES = [
    " ███████╗██████╗  ██████╗ ████████╗████████╗████████╗",
    " ██╔════╝██╔══██╗██╔═══██╗╚══██╔══╝╚══██╔══╝╚══██╔══╝",
    " ███████╗██████╔╝██║   ██║   ██║      ██║      ██║   ",
    " ╚════██║██╔═══╝ ██║   ██║   ██║      ██║      ██║   ",
    " ███████║██║     ╚██████╔╝   ██║      ██║      ██║   ",
    " ╚══════╝╚═╝      ╚═════╝    ╚═╝      ╚═╝      ╚═╝   ",
]

# Compact logo for narrow terminals
LOGO_SMALL = [
    f" {BOLD}S P O T T T{RESET}",
]


class SpotttUI:
    def __init__(self):
        self.width, self.height = shutil.get_terminal_size((80, 24))
        self._frame_buf = []
        self._needs_clear = True  # full clear on first frame & resize

    def _out(self, line: str = ""):
        # Only add lines that fit within terminal height
        if len(self._frame_buf) < self.height:
            self._frame_buf.append(line)

    def _flush(self):
        # Truncate to terminal height to prevent scrolling
        lines = self._frame_buf[:self.height]
        # Pad remaining lines with blanks to fill entire screen
        while len(lines) < self.height:
            lines.append("")
        output = "\n".join(lines)
        # On resize: full clear to wipe all artifacts
        prefix = ""
        if self._needs_clear:
            prefix = "\033[2J"
            self._needs_clear = False
        # Move cursor home, write frame, clear any trailing garbage
        sys.stdout.write(f"{prefix}\033[H{output}\033[J")
        sys.stdout.flush()
        self._frame_buf.clear()

    def invalidate(self):
        """Mark screen dirty — next flush will do a full clear."""
        self._needs_clear = True

    def _center(self, text: str) -> str:
        return _pad_center(text, self.width)

    # ── Box frame helpers (with optional glow) ───────────────────────────

    def _border_color(self, glow: float = 0.0) -> str:
        """Return border ANSI color. glow 0–1 blends from DARK gray to GREEN."""
        if glow <= 0.05:
            return DARK
        r = int(60 + (30 - 60) * glow)
        g = int(60 + (215 - 60) * glow)
        b = int(60 + (96 - 60) * glow)
        return _rgb(r, g, b)

    def _box_top(self, inner_w: int, glow: float = 0.0) -> str:
        bc = self._border_color(glow)
        return f"{bc}╭{'─' * inner_w}╮{RESET}"

    def _box_mid(self, content: str, inner_w: int, glow: float = 0.0) -> str:
        bc = self._border_color(glow)
        vlen = _visible_len(content)
        pad = max(0, inner_w - vlen)
        return f"{bc}│{RESET}{content}{' ' * pad}{bc}│{RESET}"

    def _box_sep(self, inner_w: int, glow: float = 0.0) -> str:
        bc = self._border_color(glow)
        return f"{bc}├{'─' * inner_w}┤{RESET}"

    def _box_bot(self, inner_w: int, glow: float = 0.0) -> str:
        bc = self._border_color(glow)
        return f"{bc}╰{'─' * inner_w}╯{RESET}"

    # ── Spectrum bar ─────────────────────────────────────────────────────

    def _render_spectrum(self, bands: list, inner_w: int, energy: float) -> str:
        """Render a colorful spectrum bar from band levels (0–1 each)."""
        n = len(bands)
        if n == 0:
            return ""

        # Map bands to fill the available width
        bar_w = inner_w - 4  # 2 padding each side
        chars_per_band = max(1, bar_w // n)
        total_chars = chars_per_band * n

        parts = []
        for i, level in enumerate(bands):
            level = max(0.0, min(1.0, level))
            idx = int(level * (len(_BAR_CHARS) - 1))
            ch = _BAR_CHARS[idx]

            # Color gradient: green (bass) → cyan (mid) → magenta (treble)
            t = i / max(1, n - 1)
            if t < 0.5:
                # green → cyan
                s = t * 2
                r = int(30 * (1 - s) + 0 * s)
                g = int(215 * (1 - s) + 210 * s)
                b = int(96 * (1 - s) + 210 * s)
            else:
                # cyan → magenta
                s = (t - 0.5) * 2
                r = int(0 * (1 - s) + 180 * s)
                g = int(210 * (1 - s) + 80 * s)
                b = int(210 * (1 - s) + 220 * s)

            # Brighten with energy
            brightness = 0.5 + 0.5 * energy
            r = min(255, int(r * brightness))
            g = min(255, int(g * brightness))
            b = min(255, int(b * brightness))

            color = _rgb(r, g, b)
            parts.append(f"{color}{ch * chars_per_band}")

        spectrum = "".join(parts) + RESET

        # Pad to center within box
        spec_vlen = chars_per_band * n
        left_pad = max(0, (inner_w - spec_vlen) // 2)
        return " " * left_pad + spectrum

    # ── Main render ──────────────────────────────────────────────────────

    def render(self, track, ascii_art: str, style_name: str, is_playing: bool,
               rhythm=None):
        self.width, self.height = shutil.get_terminal_size((80, 24))
        panel_w = min(76, self.width - 4)
        inner_w = panel_w

        # Rhythm values (defaults if no rhythm engine)
        glow = 0.0
        bands = []
        energy = 0.0
        pulse = 1.0
        if rhythm:
            glow = rhythm.border_brightness
            bands = rhythm.bands
            energy = rhythm.energy
            pulse = rhythm.pulse

        self._frame_buf.clear()

        # Calculate content height and center vertically
        logo = LOGO_LINES if self.width >= 60 else LOGO_SMALL
        content_h = len(logo) + 1  # logo + blank
        if track:
            art_lines = ascii_art.split("\n") if ascii_art else []
            # track info(3) + progress(2) + spectrum(3) + art + footer(4) + help(2)
            content_h += 3 + 2 + 3 + len(art_lines) + 4 + 2
        else:
            content_h += 6  # idle box

        # If content exceeds terminal, limit art lines
        max_art_lines = self.height - (content_h - len(art_lines if track and ascii_art else []))
        if track and ascii_art and max_art_lines < len(art_lines):
            art_lines_limited = max(3, max_art_lines)
        else:
            art_lines_limited = None  # no limit needed

        top_pad = max(0, (self.height - content_h) // 3)
        for _ in range(top_pad):
            self._out()

        # Logo — pulse brightness on beat
        logo_r = int(30 + (255 - 30) * glow * 0.3)
        logo_g = int(215 + (255 - 215) * glow * 0.3)
        logo_b = int(96 + (255 - 96) * glow * 0.3)
        logo_color = _rgb(logo_r, logo_g, logo_b)
        for line in logo:
            self._out(self._center(f"{logo_color}{line}{RESET}"))
        self._out()

        if track is None:
            self._render_idle(inner_w)
        else:
            self._render_playing(track, ascii_art, style_name, is_playing,
                                 inner_w, glow, bands, energy, pulse)

        self._flush()

    def _render_idle(self, inner_w: int):
        self._out(self._center(self._box_top(inner_w)))
        self._out(self._center(self._box_mid("", inner_w)))
        msg1 = f"{DIMWHITE} ♫  Waiting for Spotify...{RESET}"
        self._out(self._center(self._box_mid(msg1, inner_w)))
        msg2 = f"{GRAY}    Play something to see ASCII art here{RESET}"
        self._out(self._center(self._box_mid(msg2, inner_w)))
        self._out(self._center(self._box_mid("", inner_w)))
        self._out(self._center(self._box_bot(inner_w)))
        self._out()
        self._out(self._center(f"{DARK}q Quit{RESET}"))

    def _render_playing(self, track, ascii_art, style_name, is_playing,
                        inner_w, glow, bands, energy, pulse):
        g = glow  # shorthand

        self._out(self._center(self._box_top(inner_w, g)))

        # ── Track info ───────────────────────────────────────────────
        status_icon = f"{GREEN}▶{RESET}" if is_playing else f"{GRAY}⏸{RESET}"
        title = _truncate(track.name, inner_w - 6)
        # Pulse title brightness on beat
        tw = min(255, int(255 * pulse))
        title_color = _rgb(tw, tw, tw)
        title_line = f" {status_icon} {title_color}{BOLD}{title}{RESET}"
        self._out(self._center(self._box_mid(title_line, inner_w, g)))

        artist = _truncate(track.artist_display, inner_w - 6)
        artist_line = f"   {CYAN}{artist}{RESET}"
        self._out(self._center(self._box_mid(artist_line, inner_w, g)))

        if track.album:
            album = _truncate(track.album, inner_w - 6)
            album_line = f"   {GRAY}{album}{RESET}"
            self._out(self._center(self._box_mid(album_line, inner_w, g)))

        # ── Progress bar (pulse green brightness) ────────────────────
        self._out(self._center(self._box_mid("", inner_w, g)))
        progress_line = self._progress_bar(track, inner_w - 4, pulse)
        self._out(self._center(self._box_mid(f"  {progress_line}", inner_w, g)))

        # ── Separator ────────────────────────────────────────────────
        self._out(self._center(self._box_sep(inner_w, g)))

        # ── ASCII art ────────────────────────────────────────────────
        if ascii_art:
            art_lines = ascii_art.split("\n")
            for art_line in art_lines[:self.height]:
                art_vlen = _visible_len(art_line)
                art_pad = max(0, (inner_w - art_vlen) // 2)
                padded = " " * art_pad + art_line
                self._out(self._center(self._box_mid(padded, inner_w, g)))
        else:
            self._out(self._center(self._box_mid("", inner_w, g)))
            loading = f"{DIM}   Loading album art...{RESET}"
            self._out(self._center(self._box_mid(loading, inner_w, g)))
            self._out(self._center(self._box_mid("", inner_w, g)))

        # ── Bottom ───────────────────────────────────────────────────
        self._out(self._center(self._box_sep(inner_w, g)))

        # Style + BPM indicator
        source = "Album Cover" if track.album_images else "Artist"
        style_pill = f"{MAGENTA}{style_name}{RESET}"

        # Beat dot — blinks on each beat
        beat_dot = f"{GREEN}●{RESET}" if glow > 0.5 else f"{DARK}○{RESET}"

        info_line = (
            f" {GRAY}Style:{RESET} {style_pill}"
            f"  {DARK}│{RESET}  {GRAY}Source:{RESET} {DIMWHITE}{source}{RESET}"
            f"  {DARK}│{RESET}  {beat_dot}"
        )
        self._out(self._center(self._box_mid(info_line, inner_w, g)))

        self._out(self._center(self._box_bot(inner_w, g)))

        # ── Help footer ──────────────────────────────────────────────
        self._out()
        help_parts = [
            f"{WHITE}s{GRAY}/{WHITE}S{GRAY} Style ",
            f"{WHITE}c{GRAY} Color ",
            f"{WHITE}+{GRAY}/{WHITE}-{GRAY} Size ",
            f"{WHITE}q{GRAY} Quit{RESET}",
        ]
        self._out(self._center(f"{GRAY}{'  '.join(help_parts)}{RESET}"))

    def _progress_bar(self, track, bar_area_w: int, pulse: float = 1.0) -> str:
        progress_ms = track.interpolated_progress_ms()
        pos_sec = progress_ms // 1000
        dur_sec = track.duration_ms // 1000

        pos_str = f"{pos_sec // 60:02d}:{pos_sec % 60:02d}"
        dur_str = f"{dur_sec // 60:02d}:{dur_sec % 60:02d}"

        time_w = 5 + 3 + 5
        bar_w = max(10, bar_area_w - time_w - 4)

        ratio = min(1.0, progress_ms / max(1, track.duration_ms))
        filled = int(ratio * bar_w)

        # Pulse green brightness
        gr = min(255, int(215 * pulse))
        gb = min(255, int(96 * pulse))
        bar_color = _rgb(30, gr, gb)

        bar = (
            f"{bar_color}{'━' * filled}●{RESET}"
            f"{DARK}{'━' * max(0, bar_w - filled)}{RESET}"
        )

        return f"{GRAY}{pos_str}{RESET} {bar} {GRAY}{dur_str}{RESET}"

    def render_error(self, message: str):
        self.width, self.height = shutil.get_terminal_size((80, 24))
        self._frame_buf.clear()
        for _ in range(self.height // 3):
            self._out()
        for line in LOGO_LINES:
            self._out(self._center(f"{GREEN}{line}{RESET}"))
        self._out()
        self._out(self._center(f"\033[38;2;255;80;80m{message}{RESET}"))
        self._flush()
