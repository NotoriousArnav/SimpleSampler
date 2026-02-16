from textual.app import App, ComposeResult
from textual.widgets import Label, Static, Button
from textual.containers import Grid
from textual.reactive import reactive
import sys
import json
import os
from simplesampler.schemas.config import Bank
from simplesampler.audio.playback import AudioPlayer

# 10 MB limit for preloading
MAX_PRELOAD_BYTES = 10 * 1024 * 1024


class Pad(Button):
    """A single drum pad widget."""

    def __init__(self, pad_config, audio_player: AudioPlayer, **kwargs):
        label = (
            f"{pad_config.name}\n({pad_config.keybind})"
            if pad_config.keybind
            else pad_config.name
        )
        super().__init__(label, id=f"pad-{pad_config.id}", **kwargs)
        self.pad_config = pad_config
        self.audio_player = audio_player
        self.styles.background = pad_config.color

    def on_button_pressed(self) -> None:
        """Play sound when pressed."""
        # Use the app's cache if available
        app = self.app
        if hasattr(app, "sample_cache") and self.pad_config.id in app.sample_cache:
            data = app.sample_cache[self.pad_config.id]
            self.audio_player.play_data(data)
        else:
            # Fallback (shouldn't really happen if configured right)
            self.audio_player.play_wave_file(self.pad_config.sample_path)


class Application(App):
    CSS = """
    Grid {
        grid-size: 4 4;
        grid-gutter: 1;
        padding: 1;
    }
    Pad {
        width: 100%;
        height: 100%;
        min-height: 5;
    }
    """

    def __init__(self, bank_path: str = "default_bank.json", **kwargs):
        super().__init__(**kwargs)
        self.bank_path = bank_path
        self.audio_player = AudioPlayer()
        self.bank_config = self.load_bank()

        # Preload samples
        print("Preloading samples...")
        self.sample_cache = {}
        total_size = 0
        for pad in self.bank_config.pads:
            if pad.sample_path and os.path.exists(pad.sample_path):
                # Check file size before loading
                try:
                    size = os.path.getsize(pad.sample_path)
                    if total_size + size <= MAX_PRELOAD_BYTES:
                        self.sample_cache[pad.id] = self.audio_player.load_wav(
                            pad.sample_path
                        )
                        total_size += size
                        print(f"Preloaded {pad.sample_path} ({size / 1024:.1f} KB)")
                    else:
                        print(f"Skipping preload for {pad.sample_path} (Cache full)")
                except Exception as e:
                    print(f"Error checking {pad.sample_path}: {e}")
        print(
            f"Preloading complete. Total cache usage: {total_size / 1024 / 1024:.2f} MB"
        )

    def load_bank(self) -> Bank:
        try:
            with open(self.bank_path, "r") as f:
                data = json.load(f)
            return Bank(**data)
        except Exception as e:
            # Fallback for now if file missing or invalid
            print(f"Error loading bank: {e}")
            return Bank(name="Empty", pads=[])

    def compose(self) -> ComposeResult:
        yield Label(f"Sampler: {self.bank_config.name}")

        with Grid():
            # Create a map of existing pads for easy lookup
            config_pads = {p.id: p for p in self.bank_config.pads}

            # Default keybinds for 4x4 grid
            default_keybinds = "1234qwerasdfzxcv"

            for i in range(16):
                if i in config_pads:
                    pad_data = config_pads[i]
                    # Assign default keybind if not present
                    if not pad_data.keybind and i < len(default_keybinds):
                        pad_data.keybind = default_keybinds[i]
                    yield Pad(pad_data, self.audio_player)
                else:
                    # Create an empty placeholder pad, but we still want to visualize the layout
                    # For now, just a static widget, but in future could be an empty "Pad"
                    yield Static("", id=f"empty-{i}")

    def on_key(self, event) -> None:
        """Global key handler for triggering pads."""
        # Check against all pads
        for node in self.query(Pad):
            if node.pad_config.keybind and event.character == node.pad_config.keybind:
                node.press()  # Visually press and trigger logic
                break

    def on_unmount(self) -> None:
        self.audio_player.cleanup()
