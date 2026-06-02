"""Transcript normalization.

Lightweight cleanup of an STT/text command: lowercase, trim filler words, collapse
whitespace. Kept conservative so we never drop the user's actual payload text.
"""
from __future__ import annotations

import re

_FILLERS = [
    r"\bum+\b",
    r"\buh+\b",
    r"\berm\b",
    r"\bplease\b",
    r"\bcould you\b",
    r"\bcan you\b",
    r"\bwould you\b",
    r"\bi want you to\b",
    r"\bi need you to\b",
    r"\bgo ahead and\b",
    r"\bfor me\b",
    r"\bhey\b",
]


def normalize(text: str) -> str:
    s = (text or "").strip()
    low = s.lower()
    for pat in _FILLERS:
        low = re.sub(pat, " ", low)
    low = re.sub(r"\s+", " ", low).strip(" .,!")
    return low or s.lower().strip()
