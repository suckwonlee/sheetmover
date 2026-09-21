"""Rendering helpers. Internal translated JSON keeps D&D semantic tags."""
from __future__ import annotations
import re

DND_DISPLAY_TAG = re.compile(r"\[([A-Za-z][A-Za-z0-9_-]*)\](.*?)\[/\1\]", re.I | re.S)

def strip_dnd_display_tags(value: str) -> str:
    """Remove only D&D pseudo-tag wrappers for final human/Roll20 display."""
    if not isinstance(value, str):
        return value
    previous = None
    result = value
    while previous != result:
        previous = result
        result = DND_DISPLAY_TAG.sub(lambda m: m.group(2), result)
    return result
