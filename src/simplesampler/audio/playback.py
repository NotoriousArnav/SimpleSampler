import pyaudio
import wave
import numpy as np
import threading
from typing import Optional, Dict, List, Tuple
import os


class AudioPlayer:
    RATE = 44100
    CHANNELS = 2
    CHUNK = 1024

    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.lock = threading.Lock()
        self.active_voices: List[Dict] = []

        # Open a single persistent stream
        self.stream = self.pa.open(
            format=pyaudio.paFloat32,
            channels=self.CHANNELS,
            rate=self.RATE,
            output=True,
            frames_per_buffer=self.CHUNK,
            stream_callback=self._callback,
        )
        self.stream.start_stream()

    def play_data(self, data: np.ndarray):
        """Adds a numpy audio buffer to the active voices list."""
        if data is None or len(data) == 0:
            return

        with self.lock:
            self.active_voices.append({"data": data, "idx": 0})

    def play_wave_file(self, file_path: str):
        """Loads and plays a wav file immediately (blocking load, immediate play)."""
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
            return
        data = self.load_wav(file_path)
        self.play_data(data)

    def cleanup(self):
        """Stops and closes the audio stream and PyAudio instance."""
        if self.stream.is_active():
            self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()

    def _callback(self, in_data, frame_count, time_info, status):
        # Create an empty output buffer
        out_data = np.zeros((frame_count, self.CHANNELS), dtype=np.float32)

        with self.lock:
            # Iterate backwards so we can remove finished voices easily
            for i in range(len(self.active_voices) - 1, -1, -1):
                voice = self.active_voices[i]
                data = voice["data"]
                idx = voice["idx"]

                # Calculate how much we can read
                remaining = len(data) - idx
                to_read = min(frame_count, remaining)

                if to_read > 0:
                    # Add sample data to output
                    chunk = data[idx : idx + to_read]
                    out_data[:to_read] += chunk
                    voice["idx"] += to_read

                # If finished, remove from list
                if voice["idx"] >= len(data):
                    self.active_voices.pop(i)

        # Apply global gain to prevent clipping when mixing multiple sounds
        out_data = out_data * 0.7

        # Hard clip to prevent wrapping overflow
        np.clip(out_data, -1.0, 1.0, out=out_data)

        return (out_data.tobytes(), pyaudio.paContinue)

    def load_wav(self, file_path: str) -> np.ndarray:
        """
        Loads a WAV file, converts to float32 stereo, and resamples to 44.1kHz.
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
                    # Pad with 0 at the BEGINNING (Little Endian LSB) -> [0, B0, B1, B2]
                    # When read as Int32, this becomes (0) + (B0<<8) + (B1<<16) + (B2<<24)
                    # This shifts the 24-bit data to the upper 24 bits of the 32-bit int, preserving sign.
                    padded = np.pad(chunks, ((0, 0), (1, 0)), mode="constant")
                    audio_int32 = np.frombuffer(padded.tobytes(), dtype=np.int32)
                    audio_float = audio_int32.astype(np.float32) / 2147483648.0
                else:
                    raise ValueError(f"Unsupported bit depth: {width * 8}-bit")

                # Reshape channels
                if channels == 1:
                    # Mono to Stereo
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
            print(f"Error loading {file_path}: {e}")
            return np.zeros((0, 2), dtype=np.float32)
