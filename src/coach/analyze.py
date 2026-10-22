"""One recording in, the coach's findings out (F2 expressions with famous
people's versions, F3 pronunciation, F5 things I probably missed, and
phrases worth keeping)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import prompts
from .personas import Persona
from .settings import Settings
from .source import Line, Material, chunks

KINDS = ("grammar", "word", "natural", "wordy")
GOAL_TEXT = {
    "work": "speaking well in work meetings: clear, concise and professional.",
    "daily": "sounding natural in everyday conversation: idiomatic, relaxed, not bookish.",
    "exam": "passing a speaking exam: accurate grammar and varied vocabulary.",
}
EXPLAIN_IN = {"zh": "Simplified Chinese", "en": "English"}


@dataclass
class PersonaLine:
    persona: str
    line: str
    note: str


@dataclass
class Expression:
    start: float
    end: float
    said: str
    better: str
    kind: str
    why: str
    importance: int
    personas: list[PersonaLine] = field(default_factory=list)


@dataclass
class Pronunciation:
    word_id: int
    word: str
    ipa: str | None
    probability: float
    tip: str
    start: float
    end: float


@dataclass
class Knowledge:
    term: str
    category: str
    explanation: str
    evidence: str
    quote: str
    speaker: str | None
    start: float
    end: float


@dataclass
class Suggestion:
    phrase: str
    meaning: str
    example: str
    source: str  # me / other
    start: float | None
    end: float | None


@dataclass
class Result:
    sentences: int = 0
    expressions: list[Expression] = field(default_factory=list)
    pronunciation: list[Pronunciation] = field(default_factory=list)
    knowledge: list[Knowledge] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    seconds: float = 0.0


def _array(items: dict, required: list[str]) -> dict:
    return {
        "type": "array",
        "items": {"type": "object", "properties": items, "required": required},
    }


def schema(with_personas: bool) -> dict:
    s, i = {"type": "string"}, {"type": "integer"}
    expression = {
        "line": i,
        "said": s,
        "better": s,
        "kind": {"type": "string", "enum": list(KINDS)},
        "why": s,
        "importance": {"type": "integer", "minimum": 1, "maximum": 5},
    }
    required = list(expression)
    if with_personas:
        expression["personas"] = _array(
            {"persona": s, "line": s, "note": s}, ["persona", "line", "note"]
        )
        required.append("personas")
    return {
        "type": "object",
        "properties": {
            "expressions": _array(expression, required),
            "pronunciation": _array({"word_id": i, "tip": s}, ["word_id", "tip"]),
            "knowledge": _array(
                {"line": i, "term": s, "category": s, "explanation": s, "evidence": s},
                ["line", "term", "category", "explanation", "evidence"],
            ),
            "suggestions": _array(
                {"line": i, "phrase": s, "meaning": s, "example": s},
                ["line", "phrase", "meaning", "example"],
            ),
        },
        "required": ["expressions", "pronunciation", "knowledge", "suggestions"],
    }


def system_prompt(settings: Settings, personas: list[Persona]) -> str:
    cards = "".join(
        f"   [{p.key}] {p.name}:\n" + "".join(f"     {ln}\n" for ln in p.style.splitlines())
        for p in personas
    )
    if not personas:
        cards = "   (none: return an empty personas list)\n"
    return (
        prompts.load(settings.language, "analyze")
        .replace("{goal}", GOAL_TEXT[settings.goal])
        .replace("{explain_in}", EXPLAIN_IN.get(settings.explain_in, settings.explain_in))
        .replace("{max_expressions}", str(settings.max_expressions))
        .replace("{personas}", cards)
    )


def user_prompt(lines: list[Line], unsure) -> str:
    out = ["Transcript:"]
    out += [
        f"L{line.n} [{'me' if line.mine else line.speaker or 'other'}] {line.text}"
        for line in lines
    ]
    if unsure:
        out += ["", "Words of mine Whisper was unsure of (for pronunciation):"]
        for w in unsure:
            heard = f", might have been: {', '.join(w.candidates[:4])}" if w.candidates else ""
            ipa = _ipa(w.word)
            ipa = f", dictionary /{ipa}/" if ipa else ""
            out.append(
                f"W{w.word_id}: {w.word.strip()} ({round(w.probability * 100)}% sure{ipa}{heard})"
                f" in: {w.sentence}"
            )
    return "\n".join(out)


def _ipa(word: str) -> str | None:
    try:
        from recorder import learn

        return learn.ipa(word)
    except Exception:  # no cmudict: tips still work without IPA
        return None


def _text(x) -> str:
    return x.strip() if isinstance(x, str) else ""


def analyze(material: Material, settings: Settings, personas: list[Persona], llm) -> Result:
    """Ask the model about one recording (in parts if it is long) and keep
    only answers that point at real lines and words."""
    result = Result(sentences=material.sentences)
    if not material.sentences:
        return result
    t0 = time.monotonic()
    system = system_prompt(settings, personas)
    keys = {p.key for p in personas}
    max_chars = max(2000, (settings.num_ctx - 4500) * 3)
    unsure = {w.word_id: w for w in material.unsure}
    parts = chunks(material.lines, max_chars)
    for i, part in enumerate(parts):
        # the pronunciation list rides along with the first part only
        answer = llm.chat(
            system, user_prompt(part, material.unsure if i == 0 else []), schema(bool(personas))
        )
        by_n = {line.n: line for line in part}
        _collect(result, answer, by_n, unsure, keys)
    result.expressions.sort(key=lambda e: -e.importance)
    result.seconds = round(time.monotonic() - t0, 1)
    return result


def _collect(result: Result, answer: dict, by_n: dict[int, Line], unsure: dict, keys: set) -> None:
    for e in answer.get("expressions") or []:
        line = by_n.get(e.get("line"))
        said, better = _text(e.get("said")), _text(e.get("better"))
        if not line or not line.mine or not said or not better or said == better:
            continue
        kind = e.get("kind") if e.get("kind") in KINDS else "natural"
        try:
            importance = min(5, max(1, int(e.get("importance", 3))))
        except (TypeError, ValueError):
            importance = 3
        lines = [
            PersonaLine(p["persona"], _text(p.get("line")), _text(p.get("note")))
            for p in e.get("personas") or []
            if isinstance(p, dict) and p.get("persona") in keys and _text(p.get("line"))
        ]
        result.expressions.append(
            Expression(
                line.start, line.end, said, better, kind, _text(e.get("why")), importance, lines
            )
        )
    for p in answer.get("pronunciation") or []:
        w = unsure.pop(p.get("word_id"), None)  # pop: once per word
        if w and _text(p.get("tip")):
            result.pronunciation.append(
                Pronunciation(
                    w.word_id,
                    w.word.strip(),
                    _ipa(w.word),
                    w.probability,
                    _text(p["tip"]),
                    w.start,
                    w.end,
                )
            )
    for k in answer.get("knowledge") or []:
        line = by_n.get(k.get("line"))
        term = _text(k.get("term"))
        if not line or line.mine or not term or not _text(k.get("explanation")):
            continue
        result.knowledge.append(
            Knowledge(
                term,
                _text(k.get("category")),
                _text(k["explanation"]),
                _text(k.get("evidence")),
                line.text.replace("⟦", "").replace("⟧", ""),
                line.speaker,
                line.start,
                line.end,
            )  # fmt: skip
        )
    for s in answer.get("suggestions") or []:
        line = by_n.get(s.get("line"))
        phrase = _text(s.get("phrase"))
        if not phrase:
            continue
        result.suggestions.append(
            Suggestion(
                phrase,
                _text(s.get("meaning")),
                _text(s.get("example")),
                "me" if line and line.mine else "other",
                line.start if line else None,
                line.end if line else None,
            )  # fmt: skip
        )
