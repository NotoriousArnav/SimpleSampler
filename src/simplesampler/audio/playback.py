import sounddevice as sd
import wave
import numpy as np
from collections import deque
import os
import sys


class _Voice:
    """Lightweight voice object for the audio callback hot path.

    Uses __slots__ for fast attribute access — dict key hashing is
    measurably slower when called thousands of times per second.
    """

    __slots__ = ("data", "idx")

    def __init__(self, data: np.ndarray):
        self.data = data
        self.idx = 0


class AudioPlayer:
    RATE = 44100
    CHANNELS = 2
    MAX_VOICES = 64  # Drop oldest voices beyond this limit

    # Absolute ceiling: ~33ms at 44100 Hz.  Keeps latency bounded
    # even if the caller passes a huge value.
    _MAX_BLOCKSIZE = 1456

    def __init__(self, blocksize: int = 256):
        self.blocksize = min(blocksize, self._MAX_BLOCKSIZE)

        # Lock-free pending queue: play_data() appends here,
        # callback drains into its own local list each cycle.
        self._pending: deque[_Voice] = deque()
        self._voices: list[_Voice] = []

        self.stream = sd.OutputStream(
            samplerate=self.RATE,
            blocksize=self.blocksize,
            channels=self.CHANNELS,
            dtype="float32",
            latency="low",
            callback=self._callback,
        )
        self.stream.start()

        latency_ms = self.stream.latency * 1000
        print(f"Audio output latency: {latency_ms:.1f}ms", file=sys.stderr)

    def play_data(self, data: np.ndarray):
        """Adds a numpy audio buffer to the pending voice queue (lock-free)."""
        if data is None or len(data) == 0:
            return
        # deque.append is atomic in CPython — no lock needed
        self._pending.append(_Voice(data))

    def play_wave_file(self, file_path: str):
        """Loads and plays a wav file immediately."""
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}", file=sys.stderr)
            return
        data = self.load_wav(file_path)
        self.play_data(data)

    def cleanup(self):
        """Stops and closes the audio stream."""
        self.stream.stop()
        self.stream.close()

    def _callback(self, outdata: np.ndarray, frames: int, time, status):
        if status:
            print(f"Audio status: {status}", file=sys.stderr)

        # Drain pending voices into our local list (lock-free reads)
        pending = self._pending
        voices = self._voices
        while pending:
            voices.append(pending.popleft())

        # Enforce voice cap — drop oldest voices first
        if len(voices) > self.MAX_VOICES:
            del voices[: len(voices) - self.MAX_VOICES]

        # Zero the output buffer
        outdata[:] = 0.0

        # Mix active voices
        i = len(voices) - 1
        while i >= 0:
            voice = voices[i]
            data = voice.data
            idx = voice.idx

            remaining = len(data) - idx
            to_read = min(frames, remaining)

            if to_read > 0:
                outdata[:to_read] += data[idx : idx + to_read]
                voice.idx += to_read

            # Remove finished voices
            if voice.idx >= len(data):
                voices.pop(i)

            i -= 1

        # Global gain to prevent clipping when mixing multiple sounds
        outdata *= 0.7

        # Hard clip
        np.clip(outdata, -1.0, 1.0, out=outdata)

    def load_wav(self, file_path: str) -> np.ndarray:
        """
        Loads a WAV file, converts to float32 stereo, and resamples to target rate.
        """
        try:
            with wave.open(file_path, "rb") as wf:
                channels = wf.getnchannels()
                rate = wf.getframerate()
                width = wf.getsampwidth()
                n_frames = wf.getnframes()

                raw_data = wf.readframes(n_frames)

                # Convert to numpy float32 -1..1
                if width == 2:
                    # 16-bit
                    audio_int16 = np.frombuffer(raw_data, dtype=np.int16)
                    audio_float = audio_int16.astype(np.float32) / 32768.0
                elif width == 1:
                    # 8-bit unsigned
                    audio_uint8 = np.frombuffer(raw_data, dtype=np.uint8)
                    audio_float = (audio_uint8.astype(np.float32) - 128.0) / 128.0
                elif width == 3:
                    # 24-bit signed
                    raw_bytes = np.frombuffer(raw_data, dtype=np.uint8)
                    chunks = raw_bytes.reshape(-1, 3)
                    padded = np.pad(chunks, ((0, 0), (1, 0)), mode="constant")
                    audio_int32 = np.frombuffer(padded.tobytes(), dtype=np.int32)
                    audio_float = audio_int32.astype(np.float32) / 2147483648.0
                else:
                    raise ValueError(f"Unsupported bit depth: {width * 8}-bit")

                # Reshape channels
                if channels == 1:
                    audio_float = np.column_stack((audio_float, audio_float))
                else:
                    audio_float = audio_float.reshape(-1, channels)

                # Resample if necessary
                if rate != self.RATE:
                    duration = n_frames / rate
                    new_n_frames = int(duration * self.RATE)

                    x_old = np.linspace(0, n_frames, n_frames)
                    x_new = np.linspace(0, n_frames, new_n_frames)

                    resampled = np.zeros((new_n_frames, 2), dtype=np.float32)
                    resampled[:, 0] = np.interp(x_new, x_old, audio_float[:, 0])
                    resampled[:, 1] = np.interp(x_new, x_old, audio_float[:, 1])

                    audio_float = resampled

                return audio_float

        except Exception as e:
            print(f"Error loading {file_path}: {e}", file=sys.stderr)
            return np.zeros((0, 2), dtype=np.float32)
