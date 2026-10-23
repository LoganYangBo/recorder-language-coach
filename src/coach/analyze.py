"""What the model is asked, in two passes.

find: one recording in, the things worth a look out (F2 expression
candidates, F5 things I probably missed, phrases worth keeping). Kept short:
most candidates never make the day's top list.

polish: one day's top expressions and least-sure words in, the explanations,
famous people's versions (F2) and pronunciation tips (F3) out.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import prompts
from .personas import Persona
from .settings import Settings
from recorder import UnsureWord

from .source import Line, Material, chunks

KINDS = ("grammar", "word", "natural", "wordy")
GOAL_TEXT = {
    "work": "speaking well in work meetings: clear, concise and professional.",
    "daily": "sounding natural in everyday conversation: idiomatic, relaxed, not bookish.",
    "exam": "passing a speaking exam: accurate grammar and varied vocabulary.",
}
EXPLAIN_IN = {"zh": "Simplified Chinese", "en": "English"}
FIND_PER_PART = 5  # expression candidates asked for per part of a recording


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
    context: str = ""  # the whole line, for the polish pass
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
    knowledge: list[Knowledge] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class Polish:
    why: dict[int, str] = field(default_factory=dict)  # expression id -> why
    personas: dict[int, list[PersonaLine]] = field(default_factory=dict)
    pronunciation: list[Pronunciation] = field(default_factory=list)
    seconds: float = 0.0


def _array(items: dict, required: list[str]) -> dict:
    return {
        "type": "array",
        "items": {"type": "object", "properties": items, "required": required},
    }


STR, INT = {"type": "string"}, {"type": "integer"}


def find_schema() -> dict:
    expression = {
        "line": INT,
        "said": STR,
        "better": STR,
        "kind": {"type": "string", "enum": list(KINDS)},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5},
    }
    return {
        "type": "object",
        "properties": {
            "expressions": _array(expression, list(expression)),
            "knowledge": _array(
                {"line": INT, "term": STR, "category": STR, "explanation": STR, "evidence": STR},
                ["line", "term", "category", "explanation", "evidence"],
            ),
            "suggestions": _array(
                {"line": INT, "phrase": STR, "meaning": STR, "example": STR},
                ["line", "phrase", "meaning", "example"],
            ),
        },
        "required": ["expressions", "knowledge", "suggestions"],
    }


def polish_schema(with_personas: bool) -> dict:
    item = {"id": INT, "why": STR}
    if with_personas:
        item["personas"] = _array(
            {"persona": STR, "line": STR, "note": STR}, ["persona", "line", "note"]
        )
    return {
        "type": "object",
        "properties": {
            "items": _array(item, list(item)),
            "pronunciation": _array({"word_id": INT, "tip": STR}, ["word_id", "tip"]),
        },
        "required": ["items", "pronunciation"],
    }


def _fill(text: str, settings: Settings) -> str:
    return text.replace("{goal}", GOAL_TEXT[settings.goal]).replace(
        "{explain_in}", EXPLAIN_IN.get(settings.explain_in, settings.explain_in)
    )


def find_prompt(settings: Settings) -> str:
    text = prompts.load(settings.language, "find")
    return _fill(text, settings).replace("{max_expressions}", str(FIND_PER_PART))


def polish_prompt(settings: Settings, personas: list[Persona]) -> str:
    cards = "".join(
        f"   [{p.key}] {p.name}:\n" + "".join(f"     {ln}\n" for ln in p.style.splitlines())
        for p in personas
    )
    if not personas:
        cards = "   (none: leave personas out)\n"
    return _fill(prompts.load(settings.language, "polish"), settings).replace("{personas}", cards)


def transcript(lines: list[Line]) -> str:
    out = ["Transcript:"]
    out += [
        f"L{line.n} [{'me' if line.mine else line.speaker or 'other'}] {line.text}"
        for line in lines
    ]
    return "\n".join(out)


def polish_input(items: list[dict], words: list[UnsureWord]) -> str:
    out = ["Corrections:"]
    for e in items:
        out.append(
            f'id {e["id"]} ({e["kind"]}): said "{e["said"]}" -> better "{e["better"]}"'
            + (f"\n   whole sentence: {e['context']}" if e.get("context") else "")
        )
    if not items:
        out.append("(none)")
    out += ["", "Unsure words:"]
    for w in words:
        heard = f", might have been: {', '.join(w.candidates[:4])}" if w.candidates else ""
        ipa = _ipa(w.word)
        ipa = f", dictionary /{ipa}/" if ipa else ""
        out.append(
            f"W{w.word_id}: {w.word.strip()} ({round(w.probability * 100)}% sure{ipa}{heard})"
            f" in: {w.sentence}"
        )
    if not words:
        out.append("(none)")
    return "\n".join(out)


def _ipa(word: str) -> str | None:
    try:
        from recorder import learn

        return learn.ipa(word)
    except Exception:  # no cmudict: tips still work without IPA
        return None


def _text(x) -> str:
    return x.strip() if isinstance(x, str) else ""


def find(material: Material, settings: Settings, llm) -> Result:
    """First pass over one recording (in parts if it is long); keeps only
    answers that point at real lines."""
    result = Result(sentences=material.sentences)
    if not material.sentences:
        return result
    t0 = time.monotonic()
    system = find_prompt(settings)
    max_chars = max(2000, (settings.num_ctx - 2500) * 3)
    for part in chunks(material.lines, max_chars):
        if not any(line.mine for line in part):
            continue  # nothing of mine to coach; knowledge alone isn't worth a call
        answer = llm.chat(system, transcript(part), find_schema())
        _collect(result, answer, {line.n: line for line in part})
    result.expressions.sort(key=lambda e: -e.importance)
    result.seconds = round(time.monotonic() - t0, 1)
    return result


def polish(
    items: list[dict], words: list[UnsureWord], settings: Settings, personas: list[Persona], llm
) -> Polish:
    """Second pass, over a day's top expressions (store rows) and unsure words."""
    out = Polish()
    if not items and not words:
        return out
    t0 = time.monotonic()
    answer = llm.chat(
        polish_prompt(settings, personas), polish_input(items, words), polish_schema(bool(personas))
    )
    ids, keys = {e["id"] for e in items}, {p.key for p in personas}
    for it in answer.get("items") or []:
        if it.get("id") not in ids:
            continue
        out.why[it["id"]] = _text(it.get("why"))
        out.personas[it["id"]] = [
            PersonaLine(p["persona"], _text(p.get("line")), _text(p.get("note")))
            for p in it.get("personas") or []
            if isinstance(p, dict) and p.get("persona") in keys and _text(p.get("line"))
        ]
    by_id = {w.word_id: w for w in words}
    for p in answer.get("pronunciation") or []:
        w = by_id.pop(p.get("word_id"), None)  # pop: once per word
        if w and _text(p.get("tip")):
            out.pronunciation.append(
                Pronunciation(
                    w.word_id,
                    w.word.strip(),
                    _ipa(w.word),
                    w.probability,
                    _text(p["tip"]),
                    w.start,
                    w.end,
                )  # fmt: skip
            )
    out.seconds = round(time.monotonic() - t0, 1)
    return out


def _collect(result: Result, answer: dict, by_n: dict[int, Line]) -> None:
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
        context = line.text.replace("⟦", "").replace("⟧", "")
        result.expressions.append(
            Expression(line.start, line.end, said, better, kind, "", importance, context)
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
