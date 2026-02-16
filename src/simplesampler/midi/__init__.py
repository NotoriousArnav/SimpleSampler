"""MIDI utilities for SimpleSampler."""

import re
from typing import Optional

import mido

# Pattern: "note:36:ch9", "cc:1:ch0", "pc:5:ch0"
_MIDIBIND_RE = re.compile(r"^(?P<type>note|cc|pc):(?P<number>\d+):ch(?P<channel>\d+)$")


def parse_midibind(s: str) -> Optional[tuple[str, int, int]]:
    """Parse a midibind string into (type, number, channel) or None if invalid.

    Examples:
        parse_midibind("note:36:ch9")  -> ("note", 36, 9)
        parse_midibind("cc:1:ch0")     -> ("cc", 1, 0)
        parse_midibind("bad")          -> None
    """
    m = _MIDIBIND_RE.match(s)
    if not m:
        return None
    return (m.group("type"), int(m.group("number")), int(m.group("channel")))


def midi_msg_matches(msg: mido.Message, bind: tuple[str, int, int]) -> bool:
    """Check if an incoming mido Message matches a parsed midibind tuple.

    Args:
        msg: A mido MIDI message.
        bind: A (type, number, channel) tuple from parse_midibind().

    Returns:
        True if the message matches the binding.
    """
    bind_type, bind_number, bind_channel = bind

    if bind_type == "note":
        if msg.type == "note_on" and msg.velocity > 0:
            return msg.note == bind_number and msg.channel == bind_channel
    elif bind_type == "cc":
        if msg.type == "control_change":
            return msg.control == bind_number and msg.channel == bind_channel
    elif bind_type == "pc":
        if msg.type == "program_change":
            return msg.program == bind_number and msg.channel == bind_channel

    return False
