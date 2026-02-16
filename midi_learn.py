#!/usr/bin/env python3
"""
midi_learn.py - Capture a MIDI event and output a midibind string.

Listens on a MIDI input device for a single event (note, CC, etc.)
and prints a compact midibind string to stdout for use in bank JSON configs.

Usage:
    python midi_learn.py              # auto-select first device, capture one event
    python midi_learn.py -l           # list available MIDI input devices
    python midi_learn.py -d 0         # use device by index
    python midi_learn.py -d "MPD218"  # use device by name (substring match)

midibind format: <type>:<number>:ch<channel>
    note:36:ch9    -> Note 36 on channel 9 (drum pad)
    cc:1:ch0       -> CC #1 on channel 0 (mod wheel / knob)
    pc:5:ch0       -> Program change 5 on channel 0
"""

import argparse
import sys
import time

import mido


# MIDI message types worth capturing as bindings
BINDABLE_TYPES = {"note_on", "control_change", "program_change"}

# System / housekeeping messages to silently ignore
IGNORED_TYPES = {
    "clock",
    "active_sensing",
    "start",
    "stop",
    "continue",
    "reset",
    "sysex",
    "quarter_frame",
    "songpos",
    "song_select",
}


def format_midibind(msg: mido.Message) -> str:
    """Convert a mido Message to a compact midibind string."""
    ch = msg.channel
    if msg.type == "note_on":
        return f"note:{msg.note}:ch{ch}"
    elif msg.type == "control_change":
        return f"cc:{msg.control}:ch{ch}"
    elif msg.type == "program_change":
        return f"pc:{msg.program}:ch{ch}"
    else:
        return f"{msg.type}:ch{ch}"


def list_devices() -> list[str]:
    """Print available MIDI input ports and return the list."""
    names = mido.get_input_names()
    if not names:
        print("No MIDI input devices found.", file=sys.stderr)
        return []
    for i, name in enumerate(names):
        print(f"  [{i}] {name}")
    return names


def resolve_device(device_arg: str | None) -> str:
    """Resolve -d argument to a port name. Accepts index or substring match."""
    names = mido.get_input_names()
    if not names:
        print("Error: No MIDI input devices found.", file=sys.stderr)
        sys.exit(1)

    # No device specified — use first available
    if device_arg is None:
        print(f"Using: {names[0]}", file=sys.stderr)
        return names[0]

    # Try as numeric index
    try:
        idx = int(device_arg)
        if 0 <= idx < len(names):
            print(f"Using: {names[idx]}", file=sys.stderr)
            return names[idx]
        else:
            print(
                f"Error: Device index {idx} out of range (0-{len(names) - 1}).",
                file=sys.stderr,
            )
            sys.exit(1)
    except ValueError:
        pass

    # Try as substring match
    matches = [n for n in names if device_arg.lower() in n.lower()]
    if len(matches) == 1:
        print(f"Using: {matches[0]}", file=sys.stderr)
        return matches[0]
    elif len(matches) > 1:
        print(f"Error: Ambiguous device name '{device_arg}'. Matches:", file=sys.stderr)
        for m in matches:
            print(f"  {m}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Error: No device matching '{device_arg}'.", file=sys.stderr)
        sys.exit(1)


def capture(port_name: str) -> None:
    """Listen for MIDI events and print midibind strings continuously."""
    print("Listening for MIDI events... (Ctrl+C to quit)", file=sys.stderr)

    with mido.open_input(port_name) as inport:
        try:
            while True:
                for msg in inport.iter_pending():
                    # Skip system / housekeeping
                    if msg.type in IGNORED_TYPES:
                        continue

                    # Skip note releases
                    if msg.type == "note_off":
                        continue
                    if msg.type == "note_on" and msg.velocity == 0:
                        continue

                    if msg.type in BINDABLE_TYPES:
                        print(format_midibind(msg))
                        continue

                    # Non-bindable but not ignored — inform and keep waiting
                    print(f"(skipped: {msg})", file=sys.stderr)

                time.sleep(0.005)
        except KeyboardInterrupt:
            print("\nDone.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Capture a MIDI event and output a midibind string.",
        epilog=(
            "midibind format: <type>:<number>:ch<channel>\n"
            "  note:36:ch9   Note 36 on channel 9\n"
            "  cc:1:ch0      CC #1 on channel 0\n"
            "  pc:5:ch0      Program change 5 on channel 0"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List available MIDI input devices and exit.",
    )
    parser.add_argument(
        "-d",
        "--device",
        type=str,
        default=None,
        help="MIDI input device (index number or name substring).",
    )
    args = parser.parse_args()

    if args.list:
        names = list_devices()
        if not names:
            sys.exit(1)
        sys.exit(0)

    port_name = resolve_device(args.device)
    capture(port_name)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)
