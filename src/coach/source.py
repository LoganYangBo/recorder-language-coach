"""What the coach reads from recorder.db, through the SDK's Recorder API only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from recorder import Recorder, UnsureWord

from .lang import detector

UNSURE_BELOW = 0.6  # Whisper probability under which a word is marked as unsure


@dataclass
class Line:
    n: int  # L-number shown to the model, from 1
    speaker: str | None
    text: str
    start: float
    end: float

    @property
    def mine(self) -> bool:
        return self.speaker == "me"


@dataclass
class Material:
    recording_id: int
    day: str | None  # YYYY-MM-DD
    lines: list[Line] = field(default_factory=list)  # lines in the target language

    @property
    def sentences(self) -> int:
        """My lines in the target language: what there is to coach."""
        return sum(line.mine for line in self.lines)


def _mark_unsure(text: str, words: list[UnsureWord]) -> str:
    for w in words:
        bare = w.word.strip(" ,.?!;:\"'")
        if bare:
            text = re.sub(rf"(?<![\w⟦]){re.escape(bare)}(?![\w⟧])", f"⟦{bare}⟧", text, count=1)
    return text


def load_material(rec: Recorder, recording_id: int, language: str = "en") -> Material:
    is_target = detector(language)
    r = next((r for r in rec.recordings() if r.id == recording_id), None)
    if r is None:
        raise KeyError(f"no recording {recording_id}")
    day = r.started_at[:10] if r.started_at else None
    unsure = rec.unsure_words(recording=recording_id, below=UNSURE_BELOW, include_reviewed=True)
    m = Material(recording_id, day)
    for u in rec.utterances(recording=recording_id):
        if not is_target(u.text):
            continue
        inside = [w for w in unsure if u.start <= w.start < u.end and w.speaker == u.speaker]
        m.lines.append(
            Line(len(m.lines) + 1, u.speaker, _mark_unsure(u.text, inside), u.start, u.end)
        )
    return m


def day_unsure(rec: Recorder, day: str, language: str = "en", limit: int = 10) -> list:
    """My least-sure words of the day in target-language sentences, one per
    word, for the pronunciation tips."""
    is_target = detector(language)
    words = rec.unsure_words(day=day, speaker="me", below=UNSURE_BELOW, include_reviewed=True)
    out, seen = [], set()
    for w in sorted(words, key=lambda w: w.probability):
        key = w.word.strip(" ,.?!;:\"'").lower()
        if w.corrected_from is None and key and key not in seen and is_target(w.sentence):
            seen.add(key)
            out.append(w)
    return out[:limit]


def chunks(lines: list[Line], max_chars: int) -> list[list[Line]]:
    """Split a long conversation into parts the model's context can hold."""
    out: list[list[Line]] = [[]]
    size = 0
    for line in lines:
        n = len(line.text) + 16
        if out[-1] and size + n > max_chars:
            out.append([])
            size = 0
        out[-1].append(line)
        size += n
    return [c for c in out if c]
