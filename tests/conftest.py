import re

import pytest
from recorder import Segment, Store, Transcript, Word


class FakeLLM:
    """Answers like the model would, pointing at the lines and ids it is shown.
    `answer` (a dict, or a function of system and user) replaces the find
    pass's answer; the polish pass always gets the default."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1

    def chat(self, system, user, schema):
        self.calls.append((system, user, schema))
        if "items" in schema["properties"]:
            return polish_answer(user)
        if callable(self.answer):
            return self.answer(system, user)
        if self.answer is not None:
            return self.answer
        return {
            "expressions": [
                {
                    "line": 1,
                    "said": "discuss about the timeline",
                    "better": "discuss the timeline",
                    "kind": "grammar",
                    "importance": 4,
                },
                {"line": 2, "said": "x", "better": "y", "kind": "grammar", "importance": 2},
                {"line": 99, "said": "a", "better": "b", "kind": "word", "importance": 5},
            ],
            "knowledge": [
                {
                    "line": 2,
                    "term": "Hail Mary",
                    "category": "American football",
                    "explanation": "孤注一掷",
                    "evidence": "你问了 Sorry, what?",
                },
                {"line": 1, "term": "mine", "category": "", "explanation": "x", "evidence": ""},
            ],
            "suggestions": [
                {
                    "line": 2,
                    "phrase": "circle back",
                    "meaning": "回头再聊",
                    "example": "Let's circle back.",
                }
            ],
        }


def polish_answer(user):
    ids = [int(x) for x in re.findall(r"^id (\d+)", user, re.M)]
    words = [int(x) for x in re.findall(r"^W(\d+):", user, re.M)]
    return {
        "items": [
            {
                "id": i,
                "why": "discuss 后面直接跟宾语",
                "personas": [
                    {"persona": "jobs", "line": "Let's talk timeline.", "note": "短"},
                    {"persona": "nobody", "line": "dropped", "note": "unknown persona"},
                ],
            }
            for i in ids
        ]
        + [{"id": 9999, "why": "not asked"}],
        "pronunciation": [{"word_id": w, "tip": "th 要咬舌"} for w in words]
        + [{"word_id": 999, "tip": "?"}],
    }


@pytest.fixture
def recorder_db(tmp_path):
    db = tmp_path / "recorder.db"
    with Store(db) as store:
        segs = [
            Segment(
                0,
                3,
                "We need to discuss about the timeline thoroughly",
                "me",
                words=[
                    Word(0, 0.5, " We", 0.9),
                    Word(2.0, 2.6, " thoroughly", 0.3),
                ],
            ),
            Segment(3.5, 6, "At this point it's kind of a Hail Mary.", "Alice"),
            Segment(7, 8, "Sorry, what?", "me"),
            Segment(9, 10, "我们明天再说吧", "me"),
        ]
        (tmp_path / "a.wav").write_bytes(b"RIFF....WAVEfmt ")
        store.save(
            tmp_path / "a.wav", "h1", Transcript("en", 10, segs), started_at="2026-10-21T10:00:00"
        )
        (tmp_path / "b.wav").write_bytes(b"x")
        store.save(
            tmp_path / "b.wav",
            "h2",
            Transcript("zh", 5, [Segment(0, 2, "全是中文的一段录音", "me")]),
            started_at="2026-10-21T15:00:00",
        )
    return db
