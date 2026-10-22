import pytest
from recorder import Segment, Store, Transcript, Word


class FakeLLM:
    """Answers like the model would, pointing at the lines it is shown."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    def chat(self, system, user, schema):
        self.calls.append((system, user, schema))
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
                    "why": "discuss 后面直接跟宾语",
                    "importance": 4,
                    "personas": [
                        {"persona": "jobs", "line": "Let's talk timeline.", "note": "短"},
                        {"persona": "nobody", "line": "dropped", "note": "unknown persona"},
                    ],
                },
                {
                    "line": 2,
                    "said": "x",
                    "better": "y",
                    "kind": "grammar",
                    "why": "",
                    "importance": 2,
                },
                {
                    "line": 99,
                    "said": "a",
                    "better": "b",
                    "kind": "word",
                    "why": "",
                    "importance": 5,
                },
            ],
            "pronunciation": [{"word_id": 2, "tip": "th 要咬舌"}, {"word_id": 999, "tip": "?"}],
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
