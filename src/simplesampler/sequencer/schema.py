"""
Pydantic models for sequencer pattern files.

Pattern JSON structure:
{
  "bpm": 120,
  "time_signature": [4, 4],
  "steps_per_beat": 4,
  "active_pattern": 0,
  "patterns": [
    {
      "name": "A",
      "steps": {
        "0": [1, 0, 0, 0, 1, 0, 0, 0, ...],
        "2": [0, 0, 0, 0, 1, 0, 0, 0, ...]
      }
    }
  ]
}

Keys under "steps" are pad IDs (as strings — JSON limitation).
Only pads with at least one active step need entries.
"""

import json
from pydantic import BaseModel
from typing import Dict, List, Tuple


class Pattern(BaseModel):
    name: str
    steps: Dict[str, List[int]] = {}  # pad_id (str) -> list of 0/1


class SequenceFile(BaseModel):
    bpm: int = 120
    time_signature: Tuple[int, int] = (4, 4)
    steps_per_beat: int = 4
    active_pattern: int = 0
    patterns: List[Pattern] = []

    @property
    def total_steps(self) -> int:
        """Number of steps in one bar."""
        beats_per_bar = self.time_signature[0]
        return beats_per_bar * self.steps_per_beat

    def ensure_step_lengths(self):
        """Ensure all step lists match total_steps, padding or trimming."""
        n = self.total_steps
        for pattern in self.patterns:
            for pad_id, steps in pattern.steps.items():
                if len(steps) < n:
                    steps.extend([0] * (n - len(steps)))
                elif len(steps) > n:
                    pattern.steps[pad_id] = steps[:n]

    def save(self, path: str):
        """Save sequence to JSON file."""
        with open(path, "w") as f:
            json.dump(self.model_dump(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "SequenceFile":
        """Load sequence from JSON file."""
        with open(path, "r") as f:
            data = json.load(f)
        seq = cls.model_validate(data)
        seq.ensure_step_lengths()
        return seq

    @classmethod
    def create_default(
        cls,
        bpm: int,
        time_signature: Tuple[int, int],
        steps_per_beat: int,
        pattern_count: int,
    ) -> "SequenceFile":
        """Create a new sequence file with empty patterns."""
        names = _pattern_names(pattern_count)
        patterns = [Pattern(name=n) for n in names]
        seq = cls(
            bpm=bpm,
            time_signature=time_signature,
            steps_per_beat=steps_per_beat,
            patterns=patterns,
        )
        return seq


def _pattern_names(count: int) -> list[str]:
    """Generate pattern names: A, B, C, ... Z, AA, AB, ..."""
    names = []
    for i in range(count):
        name = ""
        n = i
        while True:
            name = chr(ord("A") + n % 26) + name
            n = n // 26 - 1
            if n < 0:
                break
        names.append(name)
    return names
