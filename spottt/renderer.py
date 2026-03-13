"""ASCII art renderer — bridges to the ascii-art library's core pipeline.

Stores raw char grids and RGB color arrays so the UI can apply per-frame
brightness modulation (beat pulse) without re-running the image pipeline.
"""

import io
import os
import sys
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image

# Add ascii-art scripts to path for importing its core modules.
_ASCII_ART_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ascii-art",
    "scripts",
)
if _ASCII_ART_DIR not in sys.path:
    sys.path.insert(0, _ASCII_ART_DIR)

from core.pipeline import (
    process_image,
    process_image_for_braille,
    process_image_for_edge,
)
from core.styles import (
    classic_ascii,
    braille_style,
    block_style,
    edge_style,
    particles_style,
    DEFAULT_RAMP,
    PRESETS,
)
from core.colors import apply_color
from core.dither import apply_dither

# Available art styles
STYLES = [
    "braille",
    "block",
    "classic",
    "edge",
    "particles",
    "retro-art",
    "terminal",
]

STYLE_DESCRIPTIONS = {
    "braille": "Braille Unicode dots — highest detail",
    "block": "Block elements — chunky retro pixels",
    "classic": "Classic density ramp ASCII",
    "edge": "Sobel edge detection outline",
    "particles": "Sparse particle scatter",
    "retro-art": "CRT retro amber phosphor",
    "terminal": "Green monochrome terminal",
}


@dataclass
class ArtFrame:
    """Raw art data: char grid + RGB colors. Used for per-frame pulse."""
    chars: list          # list[list[str]]
    colors: np.ndarray   # (rows, cols, 3) uint8 RGB


class AsciiRenderer:
    def __init__(self):
        self.style_index = 0
        self._frame_cache = {}   # cache_key -> ArtFrame
        self._ansi_cache = {}    # (cache_key, pulse_level) -> str

    @property
    def current_style(self) -> str:
        return STYLES[self.style_index % len(STYLES)]

    @property
    def style_description(self) -> str:
        return STYLE_DESCRIPTIONS.get(self.current_style, "")

    def next_style(self):
        self.style_index = (self.style_index + 1) % len(STYLES)

    def prev_style(self):
        self.style_index = (self.style_index - 1) % len(STYLES)

    def set_style(self, name: str):
        name_lower = name.lower()
        for i, s in enumerate(STYLES):
            if s == name_lower:
                self.style_index = i
                return
        for i, s in enumerate(STYLES):
            if name_lower in s:
                self.style_index = i
                return

    def render_frame(
        self,
        image_bytes: bytes,
        cols: int = 60,
        track_id: str = None,
    ) -> ArtFrame:
        """Render image to raw ArtFrame (cached). Use render_with_pulse() for ANSI output."""
        cache_key = (track_id, self.current_style, cols)
        if cache_key in self._frame_cache:
            return self._frame_cache[cache_key]

        img = Image.open(io.BytesIO(image_bytes))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        elif img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (0, 0, 0))
            bg.paste(img, mask=img.split()[3])
            img = bg

        chars, colors = self._convert(img, self.current_style, cols)
        frame = ArtFrame(chars=chars, colors=colors)

        if track_id:
            self._frame_cache[cache_key] = frame

        return frame

    def render(
        self,
        image_bytes: bytes,
        cols: int = 60,
        track_id: str = None,
    ) -> str:
        """Render image to ANSI string (no pulse). For backwards compat."""
        frame = self.render_frame(image_bytes, cols, track_id)
        return self._to_ansi(frame.chars, frame.colors, 1.0)

    def render_with_pulse(self, frame: ArtFrame, pulse: float = 1.0) -> str:
        """Render ArtFrame to ANSI string with brightness pulse (0.3–1.5).

        Quantizes pulse to ~10 levels for caching.
        """
        # Quantize to reduce cache churn
        level = round(pulse * 10) / 10
        level = max(0.3, min(1.5, level))

        # Use id of frame object + level as cache key
        cache_key = (id(frame), level)
        if cache_key in self._ansi_cache:
            return self._ansi_cache[cache_key]

        result = self._to_ansi(frame.chars, frame.colors, level)

        # Keep ansi cache bounded
        if len(self._ansi_cache) > 20:
            self._ansi_cache.clear()
        self._ansi_cache[cache_key] = result

        return result

    def _convert(self, img: Image.Image, style: str, cols: int):
        """Run the ascii-art pipeline. Returns (chars, colors_rgb)."""
        color_mode = "original"
        dither = "none"
        dither_strength = 0.8
        background = "dark"

        if style in PRESETS:
            preset = PRESETS[style]
            color_mode = preset.get("color", color_mode)
            dither = preset.get("dither", dither)
            dither_strength = preset.get("dither_strength", dither_strength)

        if style == "braille":
            brightness_hi, colors_lo, char_rows, char_cols = (
                process_image_for_braille(
                    img, cols=cols, ratio="1:1", invert=False,
                    char_aspect=None, char_pixel_width=0,
                )
            )
            threshold = float(brightness_hi.mean())
            chars = braille_style(brightness_hi, threshold=threshold)
            colors = apply_color(
                brightness_hi[::4, ::2][:char_rows, :char_cols],
                colors_lo[:char_rows, :char_cols],
                mode=color_mode, background=background,
            )
        elif style == "edge":
            magnitude, direction, colors_raw, rows, cols_out = (
                process_image_for_edge(
                    img, cols=cols, ratio="1:1", invert=False,
                    char_aspect=None, char_pixel_width=0,
                )
            )
            chars = edge_style(magnitude, direction)
            if magnitude.max() > 0:
                brightness = magnitude / magnitude.max() * 255
            else:
                brightness = magnitude
            colors = apply_color(
                brightness, colors_raw, mode=color_mode, background=background,
            )
        elif style == "block":
            grid = process_image(
                img, cols=cols, ratio="1:1", invert=False,
                char_aspect=None, char_pixel_width=0,
            )
            dithered = apply_dither(grid.brightness, dither, levels=5, strength=dither_strength)
            chars = block_style(dithered)
            colors = apply_color(
                grid.brightness, grid.colors, mode=color_mode, background=background,
            )
        elif style == "particles":
            grid = process_image(
                img, cols=cols, ratio="1:1", invert=False,
                char_aspect=None, char_pixel_width=0,
            )
            chars = particles_style(grid.brightness)
            colors = apply_color(
                grid.brightness, grid.colors, mode=color_mode, background=background,
            )
        else:
            # classic + presets (retro-art, terminal)
            if style in PRESETS:
                ramp = PRESETS[style]["ramp"]
            else:
                ramp = DEFAULT_RAMP
            grid = process_image(
                img, cols=cols, ratio="1:1", invert=False,
                char_aspect=None, char_pixel_width=0,
            )
            dithered = apply_dither(
                grid.brightness, dither, levels=len(ramp), strength=dither_strength,
            )
            chars = classic_ascii(dithered, ramp=ramp)
            colors = apply_color(
                grid.brightness, grid.colors, mode=color_mode, background=background,
            )

        # Ensure dimensions match
        char_rows = len(chars)
        char_cols = len(chars[0]) if char_rows > 0 else 0
        colors = colors[:char_rows, :char_cols]

        return chars, colors

    def _to_ansi(self, chars: list, colors: np.ndarray, brightness: float) -> str:
        """Convert char grid + RGB array to ANSI string with brightness modifier."""
        reset = "\033[0m"
        lines = []
        for r, row in enumerate(chars):
            parts = []
            prev_rgb = None
            for c, ch in enumerate(row):
                if r < colors.shape[0] and c < colors.shape[1]:
                    cr = min(255, int(colors[r, c, 0] * brightness))
                    cg = min(255, int(colors[r, c, 1] * brightness))
                    cb = min(255, int(colors[r, c, 2] * brightness))
                    rgb = (cr, cg, cb)
                    if rgb != prev_rgb:
                        parts.append(f"\033[38;2;{cr};{cg};{cb}m")
                        prev_rgb = rgb
                parts.append(ch)
            parts.append(reset)
            lines.append("".join(parts))
        return "\n".join(lines)

    def clear_cache(self):
        self._frame_cache.clear()
        self._ansi_cache.clear()
