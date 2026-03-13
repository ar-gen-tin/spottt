"""Beat-synced rhythm engine — generates visual animation parameters.

Uses Spotify Audio Analysis API for real beat/loudness data when available,
falls back to BPM-based simulation otherwise.
"""

import math
import random
import time


class RhythmEngine:
    NUM_BANDS = 16

    def __init__(self):
        self.bpm = 120.0
        self.beat_phase = 0.0       # 0.0–1.0 within current beat
        self.energy = 0.5           # overall energy 0–1
        self.bands = [0.0] * self.NUM_BANDS  # spectrum band levels 0–1
        self._beats = []            # [{start, duration}, ...]
        self._segments = []         # [{start, duration, loudness_start, loudness_max}, ...]
        self._track_id = None

        # Simulation state (used when no audio analysis)
        self._sim_phases = [random.uniform(0, math.tau) for _ in range(self.NUM_BANDS)]
        self._sim_speeds = [0.6 + i * 0.15 + random.uniform(-0.1, 0.1)
                            for i in range(self.NUM_BANDS)]

    # ── Data loading ─────────────────────────────────────────────────

    def set_track(self, track_id: str, bpm: float = 120.0):
        """Reset state for a new track."""
        if track_id == self._track_id:
            return
        self._track_id = track_id
        self.bpm = max(60, min(220, bpm))
        self._beats.clear()
        self._segments.clear()
        self.energy = 0.5
        self.bands = [0.0] * self.NUM_BANDS
        # Re-randomize simulation for variety between tracks
        self._sim_phases = [random.uniform(0, math.tau) for _ in range(self.NUM_BANDS)]
        self._sim_speeds = [0.6 + i * 0.15 + random.uniform(-0.1, 0.1)
                            for i in range(self.NUM_BANDS)]

    def set_audio_analysis(self, beats: list, segments: list, tempo: float = 0):
        """Load Spotify audio analysis data."""
        self._beats = beats
        self._segments = segments
        if tempo > 0:
            self.bpm = tempo

    @property
    def has_analysis(self) -> bool:
        return bool(self._beats)

    # ── Per-frame update ─────────────────────────────────────────────

    def update(self, progress_ms: int, is_playing: bool):
        """Advance rhythm state. Call every render frame (~7 FPS)."""
        if not is_playing:
            self.energy *= 0.92
            for i in range(self.NUM_BANDS):
                self.bands[i] *= 0.85
            self.beat_phase = 0.5
            return

        t = progress_ms / 1000.0

        # Beat phase
        if self._beats:
            self._phase_from_beats(t)
        else:
            beat_dur = 60.0 / self.bpm
            self.beat_phase = (t % beat_dur) / beat_dur

        # Energy
        if self._segments:
            self._energy_from_segments(t)
        else:
            self._simulate_energy(t)

        # Spectrum bands
        self._update_bands(t)

    # ── Internal ─────────────────────────────────────────────────────

    def _phase_from_beats(self, t: float):
        # Binary search for current beat
        lo, hi = 0, len(self._beats) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            b = self._beats[mid]
            if b["start"] + b["duration"] <= t:
                lo = mid + 1
            elif b["start"] > t:
                hi = mid - 1
            else:
                self.beat_phase = (t - b["start"]) / b["duration"]
                return
        # Between beats — fallback
        beat_dur = 60.0 / self.bpm
        self.beat_phase = (t % beat_dur) / beat_dur

    def _energy_from_segments(self, t: float):
        # Binary search for current segment
        lo, hi = 0, len(self._segments) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            s = self._segments[mid]
            if s["start"] + s["duration"] <= t:
                lo = mid + 1
            elif s["start"] > t:
                hi = mid - 1
            else:
                loud = s.get("loudness_max", s.get("loudness_start", -20))
                self.energy = max(0.0, min(1.0, (loud + 35) / 35))
                return

    def _simulate_energy(self, t: float):
        envelope = max(0.0, 1.0 - self.beat_phase * 1.8) ** 0.6
        base = 0.25 + 0.15 * math.sin(t * 0.4)
        self.energy = max(0.0, min(1.0, base + 0.6 * envelope))

    def _update_bands(self, t: float):
        beat_kick = max(0.0, 1.0 - self.beat_phase * 2.5) ** 1.2

        for i in range(self.NUM_BANDS):
            phase = self._sim_phases[i]
            speed = self._sim_speeds[i]

            # Oscillating base
            base = 0.2 + 0.25 * math.sin(t * speed + phase)
            base += 0.1 * math.sin(t * speed * 1.7 + phase * 2.3)  # harmonic

            # Beat kick — stronger in bass
            bass_weight = 1.0 - (i / self.NUM_BANDS) * 0.75
            kick = beat_kick * bass_weight * self.energy

            # Subtle noise
            noise = random.uniform(-0.03, 0.03)

            target = base + kick * 0.6 + noise

            # Smooth: fast attack, slower decay
            if target > self.bands[i]:
                self.bands[i] = self.bands[i] * 0.3 + target * 0.7
            else:
                self.bands[i] = self.bands[i] * 0.65 + target * 0.35

            self.bands[i] = max(0.0, min(1.0, self.bands[i]))

    # ── Derived properties for UI ────────────────────────────────────

    @property
    def beat_intensity(self) -> float:
        """Sharp 0–1 peak on each beat, exponential decay."""
        return max(0.0, 1.0 - self.beat_phase * 1.6) ** 1.8

    @property
    def pulse(self) -> float:
        """Smooth 0.6–1.0 pulse for color/brightness modulation."""
        return 0.6 + 0.4 * self.beat_intensity * self.energy

    @property
    def border_brightness(self) -> float:
        """0–1 value for border glow on beats."""
        return 0.3 + 0.7 * self.beat_intensity * self.energy
