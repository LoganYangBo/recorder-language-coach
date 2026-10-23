from conftest import FakeLLM
from recorder import Recorder

from coach import personas
from coach.analyze import find_prompt, polish_input, polish_prompt, polish_schema, transcript
from coach.coach import Coach
from coach.lang import is_english
from coach.settings import Settings, save
from coach.source import chunks, day_unsure, load_material


def test_is_english():
    assert is_english("We need to discuss the timeline.")
    assert not is_english("Yeah.")
    assert not is_english("我们和 Mike 明天再讨论 timeline")
    assert not is_english("")


def test_material(recorder_db):
    with Recorder(recorder_db) as rec:
        m = load_material(rec, 1)
    assert m.day == "2026-10-21"
    assert [(line.n, line.speaker) for line in m.lines] == [
        (1, "me"),
        (2, "Alice"),
    ]  # 2 short/中文 lines out
    assert "⟦thoroughly⟧" in m.lines[0].text and "⟦We⟧" not in m.lines[0].text
    assert m.sentences == 1
    with Recorder(recorder_db) as rec:
        assert [w.word.strip() for w in day_unsure(rec, "2026-10-21")] == ["thoroughly"]
    with Recorder(recorder_db) as rec:
        assert load_material(rec, 2).sentences == 0


def test_chunks():
    from coach.source import Line

    lines = [Line(i, "me", "x" * 50, i, i + 1) for i in range(10)]
    parts = chunks(lines, 200)
    assert [len(p) for p in parts] == [3, 3, 3, 1]
    assert sum(parts, []) == lines


def test_personas_load_and_override(tmp_path):
    builtin = personas.load()
    assert len(builtin) == 30
    assert sum(p.gender == "female" for p in builtin.values()) == 15
    assert builtin["jobs"].name == "Steve Jobs" and "work" in builtin["jobs"].tags
    (tmp_path / "jobs.md").write_text("---\nname: Mine\ngender: male\n---\n- short\n")
    (tmp_path / "me.md").write_text("no front matter")
    mixed = personas.load([tmp_path])
    assert mixed["jobs"].name == "Mine" and mixed["me"].style == "no front matter"


def test_prompts(recorder_db):
    ps = personas.load()
    s = Settings(goal="exam", personas=["jobs"])
    text = polish_prompt(s, [ps["jobs"]])
    assert "[jobs] Steve Jobs" in text and "speaking exam" in text and "Simplified Chinese" in text
    find = find_prompt(s)
    for t in (text, find):
        assert "{" not in t  # every placeholder filled
    assert "at most 5 places" in find
    assert "personas" not in polish_schema(False)["properties"]["items"]["items"]["properties"]
    with Recorder(recorder_db) as rec:
        m = load_material(rec, 1)
        words = day_unsure(rec, "2026-10-21")
    assert "L1 [me] We need" in transcript(m.lines) and "L2 [Alice]" in transcript(m.lines)
    item = {"id": 7, "kind": "grammar", "said": "a", "better": "b", "context": "a c"}
    u = polish_input([item], words)
    assert 'id 7 (grammar): said "a" -> better "b"' in u and "W2: thoroughly (30% sure" in u


def test_run_pending_and_report(recorder_db, tmp_path):
    llm = FakeLLM()
    c = Coach(recorder_db, llm=llm)
    assert c.coach_db == tmp_path / "coach.db"
    assert c.pending() == [2, 1]  # newest first
    assert c.run_pending(log=lambda *a: None) == 2
    # recording 2 has no English: no call; then one polish call for the day
    assert len(llm.calls) == 2 and "items" in llm.calls[1][2]["properties"]
    assert llm.unloaded == 1
    assert c.pending() == []

    r = c.day_report("2026-10-21")
    assert (r["recordings"], r["analysed"], r["sentences"], r["stale"]) == (2, 2, 1, False)
    (e,) = r["expressions"]  # line 2 is not mine, line 99 doesn't exist
    assert (e["said"], e["better"], e["kind"], e["start"]) == (
        "discuss about the timeline", "discuss the timeline", "grammar", 0
    )  # fmt: skip
    assert [(p["persona"], p["name"]) for p in e["personas"]] == [("jobs", "Steve Jobs")]
    (p,) = r["pronunciation"]
    assert (p["word"], p["tip"], p["ipa"]) == ("thoroughly", "th 要咬舌", "θɝoʊli")
    (k,) = r["knowledge"]  # a term on my own line is dropped
    assert (k["term"], k["speaker"], k["quote"]) == (
        "Hail Mary",
        "Alice",
        "At this point it's kind of a Hail Mary.",
    )
    assert r["suggestions"][0]["phrase"] == "circle back" and not r["suggestions"][0]["saved"]

    c.store.mark_known("hail mary")
    c.store.add_to_corpus("circle back", "other", meaning="回头再聊")
    r = c.day_report("2026-10-21")
    assert r["knowledge"] == [] and r["suggestions"][0]["saved"]

    save(c.store, Settings(goal="daily"))
    assert c.day_report("2026-10-21")["stale"]
    c.run_recording(1)  # again, with the new settings: replaces the old findings
    r = c.day_report("2026-10-21")
    assert not r["stale"] and len(r["expressions"]) == 1
    assert r["expressions"][0]["why"] and r["expressions"][0]["personas"]  # polished again
    assert len(r["pronunciation"]) == 1  # kept, not asked for again


def test_failure_is_recorded_and_retried(recorder_db):
    def boom(system, user):
        raise RuntimeError("model fell over")

    c = Coach(recorder_db, llm=FakeLLM(boom))
    for attempt in range(3):
        assert 1 in c.pending()
        c.run_pending(log=lambda *a: None)
    assert c.pending() == []  # given up after 3 tries
    (a,) = [a for a in c.store.analyses() if a["recording_id"] == 1]
    assert (a["status"], a["attempts"]) == ("failed", 3) and "fell over" in a["error"]
    assert c.day_report("2026-10-21")["failed"][0]["recording_id"] == 1


def test_daily_limit(recorder_db):
    many = {
        "expressions": [
            {
                "line": 1,
                "said": f"s{i}",
                "better": f"b{i}",
                "kind": "word",
                "why": "",
                "importance": i % 5 + 1,
            }
            for i in range(12)
        ],
        "pronunciation": [],
        "knowledge": [],
        "suggestions": [],
    }
    c = Coach(recorder_db, llm=FakeLLM(many))
    save(c.store, Settings(max_expressions=4, personas=[]))
    c.run_pending(log=lambda *a: None)
    got = c.day_report("2026-10-21")["expressions"]
    assert len(got) == 4 and all(e["importance"] == 5 for e in got[:2])
    assert all(e["polished"] and e["why"] for e in got)  # only the top 4 were explained
    assert sum(not e["polished"] for e in c.store.expressions("2026-10-21")) == 8


def test_corpus(recorder_db):
    c = Coach(recorder_db, llm=FakeLLM())
    e = c.store.add_to_corpus(
        " give it a real shot ", "persona", persona="obama", meaning="认真试一次"
    )
    assert e["phrase"] == "give it a real shot" and e["tags"] == []
    c.store.add_to_corpus(
        "give it a real shot", "persona", meaning="认真地试一次"
    )  # same phrase: updated
    assert [x["meaning"] for x in c.store.corpus()] == ["认真地试一次"]
    c.store.add_to_corpus("circle back", "other")
    assert [x["phrase"] for x in c.store.corpus("circle")] == ["circle back"]
    assert [x["phrase"] for x in c.store.corpus(source="persona")] == ["give it a real shot"]
    assert c.store.remove_from_corpus(e["id"]) and not c.store.remove_from_corpus(e["id"])
