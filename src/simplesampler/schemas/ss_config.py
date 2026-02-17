"""
Master configuration for SimpleSampler (ss_config.toml).

Search order:
  1. $XDG_CONFIG_HOME/simplesampler/ss_config.toml
  2. ./ss_config.toml  (working directory)

If not found, all defaults apply silently.
"""

import os
import tomllib
from pydantic import BaseModel
from typing import Tuple


class MetronomeConfig(BaseModel):
    enabled: bool = True
    sound: str = ""  # Path to WAV — empty means generated sine click
    volume: float = 0.7  # 0.0 – 1.0
    accent_beat_1: bool = True  # Louder click on beat 1


class SequencerConfig(BaseModel):
    default_bpm: int = 120
    steps_per_beat: int = 4
    time_signature: Tuple[int, int] = (4, 4)
    pattern_count: int = 4  # Default number of empty patterns to create


class SSConfig(BaseModel):
    metronome: MetronomeConfig = MetronomeConfig()
    sequencer: SequencerConfig = SequencerConfig()


def load_config(override_path: str | None = None) -> SSConfig:
    """
    Load ss_config.toml from the override path, XDG config dir, or cwd.
    Returns defaults if no file is found.
    """
    paths: list[str] = []

    if override_path:
        paths.append(override_path)

    # XDG_CONFIG_HOME (default ~/.config)
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    paths.append(os.path.join(xdg, "simplesampler", "ss_config.toml"))

    # Current working directory
    paths.append(os.path.join(os.getcwd(), "ss_config.toml"))

    for path in paths:
        if os.path.isfile(path):
            with open(path, "rb") as f:
                data = tomllib.load(f)
            return SSConfig.model_validate(data)

    return SSConfig()
