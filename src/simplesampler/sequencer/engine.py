"""
Sequencer playback engine.

Daemon thread that advances through steps at BPM tempo, fires samples
via AudioPlayer, handles count-in, metronome, and bar-boundary pattern switching.
"""

import threading
import time
import math
import numpy as np
from typing import Callable

from simplesampler.audio.playback import AudioPlayer
from simplesampler.sequencer.schema import SequenceFile
from simplesampler.schemas.ss_config import MetronomeConfig


def generate_click(
    frequency: float = 1000.0,
    duration: float = 0.02,
    volume: float = 0.7,
    rate: int = 44100,
) -> np.ndarray:
    """Generate a short sine-wave click for the metronome."""
    n_samples = int(rate * duration)
    t = np.linspace(0, duration, n_samples, dtype=np.float32)
    # Sine with fast exponential decay envelope
    envelope = np.exp(-t * 40.0).astype(np.float32)
    mono = (np.sin(2.0 * math.pi * frequency * t) * envelope * volume).astype(
        np.float32
    )
    return np.column_stack((mono, mono))


class SequencerEngine:
    """Step sequencer playback engine with count-in and metronome."""

    def __init__(
        self,
        audio: AudioPlayer,
        sequence: SequenceFile,
        sample_cache: dict[int, np.ndarray],
        metronome_cfg: MetronomeConfig,
        metronome_click: np.ndarray | None = None,
        on_step: Callable[[int], None] | None = None,
        on_count_in_beat: Callable[[int], None] | None = None,
        on_playback_start: Callable[[], None] | None = None,
    ):
        self.audio = audio
        self.sequence = sequence
        self.sample_cache = sample_cache
        self.metronome_cfg = metronome_cfg
        self.on_step = on_step  # Called with current step index
        self.on_count_in_beat = on_count_in_beat  # Called with beat number (1-4)
        self.on_playback_start = on_playback_start

        # Metronome sounds
        if metronome_click is not None:
            self._click_normal = metronome_click
            self._click_accent = metronome_click  # Same if user-provided
        else:
            vol = metronome_cfg.volume
            self._click_normal = generate_click(880.0, 0.02, vol)
            self._click_accent = generate_click(1760.0, 0.02, min(vol * 1.3, 1.0))

        self._playing = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._current_step = 0

        # Pattern switching: queued index applied at bar boundary
        self._pending_pattern: int | None = None

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def current_step(self) -> int:
        return self._current_step

    def queue_pattern_switch(self, index: int):
        """Queue a pattern switch — applied at end of current bar."""
        if 0 <= index < len(self.sequence.patterns):
            self._pending_pattern = index

    def start(self):
        """Start playback with count-in."""
        if self._playing:
            return
        # Wait for previous daemon thread to finish if still alive
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop playback.

        Sets the stop event and returns immediately. The daemon thread
        will exit on its own — no join() to avoid deadlocking with
        call_from_thread in the step callback.
        """
        self._stop_event.set()
        self._playing = False
        self._current_step = 0

    def _step_interval(self) -> float:
        """Seconds per step at current BPM."""
        return 60.0 / self.sequence.bpm / self.sequence.steps_per_beat

    def _run(self):
        """Main playback loop — count-in then step through pattern."""
        interval = self._step_interval()
        beats_per_bar = self.sequence.time_signature[0]

        # --- Count-in: play metronome for one full bar of beats ---
        if self.metronome_cfg.enabled:
            beat_interval = 60.0 / self.sequence.bpm
            next_time = time.perf_counter()
            for beat in range(1, beats_per_bar + 1):
                if self._stop_event.is_set():
                    return
                # Play click
                if self.metronome_cfg.accent_beat_1 and beat == 1:
                    self.audio.play_data(self._click_accent)
                else:
                    self.audio.play_data(self._click_normal)
                if self.on_count_in_beat:
                    self.on_count_in_beat(beat)
                next_time += beat_interval
                sleep_dur = next_time - time.perf_counter()
                if sleep_dur > 0:
                    if self._stop_event.wait(timeout=sleep_dur):
                        return
        else:
            # Silent count-in — still wait one bar duration
            bar_duration = (60.0 / self.sequence.bpm) * beats_per_bar
            if self._stop_event.wait(timeout=bar_duration):
                return

        # --- Playback loop ---
        self._playing = True
        self._current_step = 0
        if self.on_playback_start:
            self.on_playback_start()

        total_steps = self.sequence.total_steps
        next_time = time.perf_counter()

        while not self._stop_event.is_set():
            # Recalculate interval in case BPM changed
            interval = self._step_interval()

            # Bar boundary: apply pending pattern switch
            if self._current_step == 0 and self._pending_pattern is not None:
                self.sequence.active_pattern = self._pending_pattern
                self._pending_pattern = None

            # Get current pattern
            pat_idx = self.sequence.active_pattern
            if 0 <= pat_idx < len(self.sequence.patterns):
                pattern = self.sequence.patterns[pat_idx]
            else:
                pattern = None

            # Fire samples for this step
            if pattern is not None:
                for pad_id_str, steps in pattern.steps.items():
                    if self._current_step < len(steps) and steps[self._current_step]:
                        pad_id = int(pad_id_str)
                        if pad_id in self.sample_cache:
                            self.audio.play_data(self.sample_cache[pad_id])

            # Metronome on beat boundaries
            if self.metronome_cfg.enabled:
                if self._current_step % self.sequence.steps_per_beat == 0:
                    beat_num = self._current_step // self.sequence.steps_per_beat + 1
                    if self.metronome_cfg.accent_beat_1 and beat_num == 1:
                        self.audio.play_data(self._click_accent)
                    else:
                        self.audio.play_data(self._click_normal)

            # Notify UI
            if self.on_step:
                self.on_step(self._current_step)

            # Advance step
            self._current_step = (self._current_step + 1) % total_steps

            # Drift-compensated sleep
            next_time += interval
            sleep_dur = next_time - time.perf_counter()
            if sleep_dur > 0:
                if self._stop_event.wait(timeout=sleep_dur):
                    break

        self._playing = False
        self._current_step = 0
