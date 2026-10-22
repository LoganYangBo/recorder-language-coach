"""Which sentences are in the language being learnt.

Recordings mix languages (a meeting in English, a call home in Chinese), so
this is decided per sentence. A language is a function from text to bool;
adding one is adding an entry to DETECTORS (and its prompts under prompts/).
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def is_english(text: str, min_words: int = 3) -> bool:
    """Mostly Latin letters (80%+ of all letters) and at least `min_words` words,
    so "Yeah." and Chinese sentences with an English name in them are skipped."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(c.isascii() for c in letters)
    return latin / len(letters) >= 0.8 and len(_WORD.findall(text)) >= min_words


DETECTORS = {"en": is_english}


def detector(language: str):
    try:
        return DETECTORS[language]
    except KeyError:
        raise ValueError(f"language {language!r} is not supported yet") from None
