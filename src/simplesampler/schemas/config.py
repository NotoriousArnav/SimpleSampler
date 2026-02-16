from pydantic import BaseModel
from typing import List, Optional


class Pad(BaseModel):
    id: int  # 0 to 15
    name: str
    sample_path: str
    color: str = "blue"  # Default textual color
    keybind: Optional[str] = None


class Bank(BaseModel):
    name: str
    pads: List[Pad]
