# SimpleSampler

A low-latency sample pad that lives in your terminal. Load any WAV, map it to a key or MIDI note, and play — no DAW required.

Built for live performance where every millisecond counts.

## Features

- **4x4 pad grid** in the terminal via [Textual](https://textual.textualize.io/) — keyboard, mouse, or MIDI trigger
- **~6ms audio latency** — 256-sample buffer with lock-free voice mixing, no mutexes in the audio path
- **MIDI input** — bind any note, CC, or program change to any pad with a simple string like `note:36:ch9`
- **Hot-swap MIDI devices** from within the app — press `m`, pick a device, keep playing
- **Instant playback** — samples preloaded into memory as float32 NumPy arrays (up to 10 MB cache)
- **Supports 8/16/24-bit WAV**, mono or stereo, any sample rate (auto-resampled to 44.1kHz)

## Requirements

- **Linux** (this is all we care about)
- **Python >= 3.13**
- **PortAudio** — the system library that makes audio go fast
  ```
  # Debian / Ubuntu
  sudo apt install libportaudio2

  # Arch
  sudo pacman -S portaudio
  ```
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager

## Installation

```bash
git clone https://github.com/yourusername/SimpleSampler.git
cd SimpleSampler
uv sync
```

That's it. No virtualenv juggling, no pip drama.

## Usage

```bash
uv run simplesampler my_bank.json
```

The app launches in your terminal with a 4x4 pad grid. Smash keys, click pads, or send MIDI — audio fires instantly.

### Controls

| Key | Action |
|-----|--------|
| `1 2 3 4` | Pads 0-3 (top row) |
| `q w e r` | Pads 4-7 |
| `a s d f` | Pads 8-11 |
| `z x c v` | Pads 12-15 |
| `m` | Open MIDI device selector |
| Mouse click | Trigger any pad |

Keybinds are customizable per-pad in the bank JSON (see below). The defaults above apply when no `keybind` is set.

## Bank Configuration

A bank is a JSON file that defines your pad layout. Here's a minimal example:

```json
{
  "name": "My Kit",
  "midi_device": "MPD218",
  "pads": [
    {
      "id": 0,
      "name": "Kick",
      "sample_path": "/path/to/kick.wav",
      "color": "red",
      "keybind": "1",
      "midibind": "note:36:ch9"
    },
    {
      "id": 1,
      "name": "Snare",
      "sample_path": "/path/to/snare.wav",
      "color": "green",
      "keybind": "2",
      "midibind": "note:38:ch9"
    }
  ]
}
```

### Bank fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Display name for the bank |
| `midi_device` | string | no | MIDI input device — name substring (e.g. `"MPD218"`) or index (e.g. `"0"`). Omit to skip MIDI on startup. |
| `pads` | array | yes | List of pad objects (up to 16) |

### Pad fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | int | yes | Pad position, 0-15 (4x4 grid, left-to-right, top-to-bottom) |
| `name` | string | yes | Display label on the pad |
| `sample_path` | string | yes | Absolute path to a WAV file |
| `color` | string | no | Pad color — any [Textual color](https://textual.textualize.io/css_types/color/) (default: `"blue"`) |
| `keybind` | string | no | Keyboard key to trigger this pad (default: positional `1234qwerasdfzxcv`) |
| `midibind` | string | no | MIDI binding string (see below) |

### Midibind format

```
note:<number>:ch<channel>    — MIDI note (e.g. note:36:ch9)
cc:<number>:ch<channel>      — Control change (e.g. cc:1:ch0)
pc:<number>:ch<channel>      — Program change (e.g. pc:5:ch0)
```

Don't know your MIDI note numbers? That's what `midi_learn.py` is for.

## MIDI Learn

`midi_learn.py` is a standalone CLI tool that listens on a MIDI device and prints midibind strings to stdout as you hit pads/keys/knobs. Copy the output straight into your bank JSON.

```bash
# List available MIDI devices
uv run python midi_learn.py -l

# Listen on a specific device (by name)
uv run python midi_learn.py -d "MPD218"

# Listen on a specific device (by index)
uv run python midi_learn.py -d 0

# Auto-select first available device
uv run python midi_learn.py
```

Hit a pad on your controller, get a string:

```
note:36:ch9
note:38:ch9
note:42:ch9
cc:1:ch0
```

Paste those into your bank JSON's `midibind` fields. Ctrl+C when you're done.

## How It's Fast

A few deliberate choices that add up:

- **sounddevice + PortAudio** — 256-sample blocks at 44.1kHz = ~5.8ms buffer. Reports actual achieved latency on startup.
- **Lock-free audio** — voices are queued via `collections.deque` (atomic append in CPython). The audio callback drains its own local list. No mutex ever touches the hot path.
- **Direct key dispatch** — `on_key` fires audio through a pre-built dict lookup, completely bypassing Textual's async widget message queue. You hear the sound before the pad even flashes.
- **MIDI daemon thread** — polls at 2ms intervals via `iter_pending()`, triggers audio directly from the thread. No round-trip through the UI event loop.
- **Preloaded samples** — WAV files are decoded, resampled, and converted to float32 stereo NumPy arrays at startup. Zero file I/O during playback.

## License

MIT
