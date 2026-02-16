from pydantic import BaseModel
from typing import List, Optional


class Pad(BaseModel):
    id: int  # 0 to 15
    name: str
    sample_path: str
    color: str = "blue"  # Default textual color
    keybind: Optional[str] = None
    midibind: Optional[str] = None  # e.g. "note:36:ch9", "cc:1:ch0"


class Bank(BaseModel):
    name: str
    pads: List[Pad]
    midi_device: Optional[str] = (
        None  # Device name substring or index, e.g. "POCO X3 Pro" or "1"
    )
