from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Label, Static, Button, OptionList
from textual.widgets.option_list import Option
from textual.containers import Grid, Vertical
import sys
import json
import os
import time
import threading
from functools import partial
from simplesampler.schemas.config import Bank
from simplesampler.audio.playback import AudioPlayer
from simplesampler.midi import parse_midibind, midi_msg_matches

import mido

# 10 MB limit for preloading
MAX_PRELOAD_BYTES = 10 * 1024 * 1024

# Duration (seconds) for pad visual feedback flash
PAD_FLASH_DURATION = 0.15


class Pad(Button):
    """A single sample pad widget."""

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
        """Play sound when clicked with mouse."""
        app = self.app
        if hasattr(app, "sample_cache") and self.pad_config.id in app.sample_cache:
            data = app.sample_cache[self.pad_config.id]
            self.audio_player.play_data(data)
        else:
            self.audio_player.play_wave_file(self.pad_config.sample_path)


# Value returned by MidiDeviceScreen when user picks "None / Disconnect"
_MIDI_NONE = ""


class MidiDeviceScreen(ModalScreen[str]):
    """Modal screen for selecting a MIDI input device.

    Dismisses with the chosen port name (str), or _MIDI_NONE to disconnect.
    """

    CSS = """
    MidiDeviceScreen {
        align: center middle;
    }
    #midi-select-box {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #midi-select-box Label {
        width: 100%;
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    #midi-option-list {
        height: auto;
        max-height: 20;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        try:
            names = mido.get_input_names()
        except Exception:
            names = []

        options: list[Option] = [Option("None  (disconnect)", id="midi-none")]
        for name in names:
            options.append(Option(name, id=name))

        with Vertical(id="midi-select-box"):
            yield Label("Select MIDI Device")
            yield OptionList(*options, id="midi-option-list")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "midi-none":
            self.dismiss(_MIDI_NONE)
        else:
            self.dismiss(option_id or _MIDI_NONE)

    def action_cancel(self) -> None:
        self.app.pop_screen()


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
    #midi-status {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("m", "midi_select", "MIDI Device"),
    ]

    def __init__(self, bank_path: str = "default_bank.json", **kwargs):
        super().__init__(**kwargs)
        self.bank_path = bank_path
        self.audio_player = AudioPlayer()
        self.bank_config = self.load_bank()

        # Build keybind lookup for fast key→pad resolution in on_key
        self._keybind_map: dict[str, int] = {}

        # Build midibind lookup: (type, number, channel) → pad_id
        self._midibind_map: dict[tuple[str, int, int], int] = {}
        for pad in self.bank_config.pads:
            if pad.midibind:
                parsed = parse_midibind(pad.midibind)
                if parsed:
                    self._midibind_map[parsed] = pad.id
                else:
                    print(
                        f"  Warning: invalid midibind '{pad.midibind}' on pad {pad.id}",
                        file=sys.stderr,
                    )

        # MIDI listener state
        self._midi_port: mido.ports.BaseInput | None = None
        self._midi_thread: threading.Thread | None = None
        self._midi_running = False
        self._active_midi_port_name: str | None = None

        # Preload samples
        print("Preloading samples...", file=sys.stderr)
        self.sample_cache: dict[int, object] = {}
        total_size = 0
        for pad in self.bank_config.pads:
            if pad.sample_path and os.path.exists(pad.sample_path):
                try:
                    size = os.path.getsize(pad.sample_path)
                    if total_size + size <= MAX_PRELOAD_BYTES:
                        self.sample_cache[pad.id] = self.audio_player.load_wav(
                            pad.sample_path
                        )
                        total_size += size
                        print(
                            f"  Loaded: {pad.name} ({size / 1024:.1f} KB)",
                            file=sys.stderr,
                        )
                    else:
                        print(f"  Skipped: {pad.name} (cache full)", file=sys.stderr)
                except Exception as e:
                    print(f"  Error: {pad.sample_path}: {e}", file=sys.stderr)
        print(
            f"Preload complete: {total_size / 1024 / 1024:.2f} MB cached",
            file=sys.stderr,
        )

    def load_bank(self) -> Bank:
        try:
            with open(self.bank_path, "r") as f:
                data = json.load(f)
            return Bank(**data)
        except Exception as e:
            print(f"Error loading bank: {e}", file=sys.stderr)
            return Bank(name="Empty", pads=[])

    def compose(self) -> ComposeResult:
        yield Label(f"Sampler: {self.bank_config.name}")

        with Grid():
            config_pads = {p.id: p for p in self.bank_config.pads}
            default_keybinds = "1234qwerasdfzxcv"

            for i in range(16):
                if i in config_pads:
                    pad_data = config_pads[i]
                    if not pad_data.keybind and i < len(default_keybinds):
                        pad_data.keybind = default_keybinds[i]
                    # Register keybind for fast lookup
                    if pad_data.keybind:
                        self._keybind_map[pad_data.keybind] = pad_data.id
                    yield Pad(pad_data, self.audio_player)
                else:
                    yield Static("", id=f"empty-{i}")

        yield Label("MIDI: not connected", id="midi-status")

    def on_key(self, event) -> None:
        """Global key handler — triggers audio directly, bypassing widget message queue."""
        key = event.character
        if key is None:
            return

        pad_id = self._keybind_map.get(key)
        if pad_id is None:
            return

        # Fire audio immediately (no async queue hops)
        cached = self.sample_cache.get(pad_id)
        if cached is not None:
            self.audio_player.play_data(cached)

        # Visual feedback: flash the pad (non-blocking, happens async after audio is triggered)
        self._flash_pad(pad_id)

    def _flash_pad(self, pad_id: int) -> None:
        """Briefly flash a pad widget for visual feedback."""
        try:
            pad_widget = self.query_one(f"#pad-{pad_id}", Pad)
            pad_widget.add_class("-active")
            self.set_timer(
                PAD_FLASH_DURATION,
                partial(pad_widget.remove_class, "-active"),
            )
        except Exception:
            pass

    def _trigger_pad(self, pad_id: int) -> None:
        """Trigger a pad's audio and visual feedback. Safe to call from any thread."""
        cached = self.sample_cache.get(pad_id)
        if cached is not None:
            self.audio_player.play_data(cached)
        # UI updates must go through Textual's thread-safe call
        self.call_from_thread(self._flash_pad, pad_id)

    # --- MIDI listener ---

    def _resolve_midi_device(self) -> str | None:
        """Resolve bank_config.midi_device to a port name. Returns None on failure."""
        device = self.bank_config.midi_device
        if device is None:
            return None  # No MIDI device configured — skip silently

        try:
            names = mido.get_input_names()
        except Exception:
            names = []

        if not names:
            print("MIDI: No input devices found.", file=sys.stderr)
            return None

        # Try as numeric index
        try:
            idx = int(device)
            if 0 <= idx < len(names):
                return names[idx]
            else:
                print(
                    f"MIDI: Device index {idx} out of range (0-{len(names) - 1}).",
                    file=sys.stderr,
                )
                return None
        except ValueError:
            pass

        # Try as case-insensitive substring match
        matches = [n for n in names if device.lower() in n.lower()]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            print(f"MIDI: Ambiguous device name '{device}'. Matches:", file=sys.stderr)
            for m in matches:
                print(f"  {m}", file=sys.stderr)
            return None
        else:
            print(f"MIDI: No device matching '{device}'.", file=sys.stderr)
            return None

    def _start_midi_listener(self) -> None:
        """Open the configured MIDI input port and start polling in a background thread."""
        if not self._midibind_map:
            return  # No midibinds configured, skip

        port_name = self._resolve_midi_device()
        if port_name is None:
            return

        try:
            self._midi_port = mido.open_input(port_name)
        except Exception as e:
            print(f"MIDI: Failed to open '{port_name}': {e}", file=sys.stderr)
            return

        print(f"MIDI: Listening on '{port_name}'", file=sys.stderr)
        self._active_midi_port_name = port_name
        self._midi_running = True
        self._midi_thread = threading.Thread(
            target=self._midi_poll_loop, daemon=True, name="midi-input"
        )
        self._midi_thread.start()

    def _midi_poll_loop(self) -> None:
        """Background thread: poll MIDI input and trigger matching pads."""
        port = self._midi_port
        if port is None:
            return

        while self._midi_running:
            try:
                for msg in port.iter_pending():
                    # Only match note_on with velocity > 0, CC, and PC
                    if msg.type == "note_off":
                        continue
                    if msg.type == "note_on" and msg.velocity == 0:
                        continue

                    for bind, pad_id in self._midibind_map.items():
                        if midi_msg_matches(msg, bind):
                            self._trigger_pad(pad_id)
                            break
            except Exception:
                if not self._midi_running:
                    break
            time.sleep(0.002)  # ~2ms poll interval

    def _stop_midi_listener(self) -> None:
        """Stop the MIDI polling thread and close the port."""
        self._midi_running = False
        if self._midi_thread is not None:
            self._midi_thread.join(timeout=1.0)
            self._midi_thread = None
        if self._midi_port is not None:
            try:
                self._midi_port.close()
            except Exception:
                pass
            self._midi_port = None
        self._active_midi_port_name = None

    def _update_midi_status(self) -> None:
        """Update the MIDI status label to reflect current connection."""
        try:
            label = self.query_one("#midi-status", Label)
        except Exception:
            return
        name = self._active_midi_port_name
        if name:
            label.update(f"MIDI: {name}  [m: change]")
        else:
            label.update("MIDI: not connected  [m: select device]")

    def _start_midi_on_port(self, port_name: str) -> bool:
        """Open a specific MIDI port and start the listener. Returns True on success."""
        if not self._midibind_map:
            print(
                "MIDI: No midibinds configured — nothing to listen for.",
                file=sys.stderr,
            )
            return False

        try:
            self._midi_port = mido.open_input(port_name)
        except Exception as e:
            print(f"MIDI: Failed to open '{port_name}': {e}", file=sys.stderr)
            return False

        print(f"MIDI: Listening on '{port_name}'", file=sys.stderr)
        self._active_midi_port_name = port_name
        self._midi_running = True
        self._midi_thread = threading.Thread(
            target=self._midi_poll_loop, daemon=True, name="midi-input"
        )
        self._midi_thread.start()
        return True

    def _switch_midi_device(self, port_name: str | None) -> None:
        """Handle result from MidiDeviceScreen. Switch to the chosen device or disconnect."""
        if port_name is None:
            return  # User cancelled (Escape) — no change

        # Stop existing listener first
        self._stop_midi_listener()

        if port_name:  # non-empty means user picked a real device
            self._start_midi_on_port(port_name)

        self._update_midi_status()

    def action_midi_select(self) -> None:
        """Open the MIDI device selection modal."""
        self.push_screen(MidiDeviceScreen(), callback=self._switch_midi_device)

    def on_mount(self) -> None:
        """Called after the app is fully composed — start MIDI listener."""
        self._start_midi_listener()
        self._update_midi_status()

    def on_unmount(self) -> None:
        self._stop_midi_listener()
        self.audio_player.cleanup()
