import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from conftest import FakeLLM

from coach.cli import main
from coach.coach import Coach
from coach.web import App, make_handler


@pytest.fixture
def server(recorder_db):
    coach = Coach(recorder_db, llm=FakeLLM())
    app = App(coach, log=lambda *a: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None, method=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base + path, data=data, method=method or ("POST" if data else "GET")
        )
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as res:
                raw = res.read()
                ctype = res.headers["Content-Type"]
                return res.status, (json.loads(raw) if "json" in ctype else raw), res.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    call.app = app
    yield call
    srv.shutdown()


def wait_idle(app):
    for _ in range(100):
        if not app.queued and app.coach.current is None:
            return
        time.sleep(0.05)
    raise AssertionError("queue never drained")


def test_page_and_days(server):
    status, html, _ = server("/")
    assert status == 200 and b"Language Coach" in html
    status, days, _ = server("/api/days")
    assert days == [{"day": "2026-10-21", "recordings": 2, "analysed": 0, "expressions": 0}]
    status, r, _ = server("/api/days/2026-10-21")
    assert r["pending"] == [1, 2] and r["expressions"] == []


def test_run_day_then_act_on_report(server):
    status, r, _ = server("/api/days/2026-10-21/run", {})
    assert r == {"queued": 2}
    wait_idle(server.app)
    _, r, _ = server("/api/days/2026-10-21")
    assert r["analysed"] == 2 and len(r["expressions"]) == 1
    eid = r["expressions"][0]["id"]

    assert server(f"/api/expressions/{eid}/dismiss", {})[0] == 200
    assert server("/api/expressions/9999/dismiss", {})[0] == 404
    assert server("/api/known", {"term": "Hail Mary"})[0] == 200
    _, r, _ = server("/api/days/2026-10-21")
    assert r["expressions"] == [] and r["knowledge"] == []

    status, e, _ = server(
        "/api/corpus",
        {"phrase": "circle back", "source": "other", "meaning": "回头再聊", "day": "2026-10-21"},
    )
    assert status == 200 and e["phrase"] == "circle back"
    assert server("/api/corpus", {"phrase": "x", "source": "bogus"})[0] == 400
    assert server("/api/corpus", {"phrase": " ", "source": "me"})[0] == 400
    _, rows, _ = server("/api/corpus?q=circle")
    assert [x["phrase"] for x in rows] == ["circle back"]
    _, md, _ = server("/api/corpus.md")
    assert b"| circle back | \xe5\x9b\x9e" in md
    assert server(f"/api/corpus/{e['id']}", method="DELETE")[0] == 200
    assert server(f"/api/corpus/{e['id']}", method="DELETE")[0] == 404


def test_settings(server):
    _, s, _ = server("/api/settings")
    assert s["settings"]["goal"] == "work" and len(s["personas"]) == 30 and s["models"] == []
    _, s, _ = server("/api/settings", {"goal": "exam", "personas": ["oprah"], "max_expressions": 5})
    assert (s["settings"]["goal"], s["settings"]["personas"], s["settings"]["max_expressions"]) == (
        "exam",
        ["oprah"],
        5,
    )
    assert server("/api/settings", {"goal": "nope"})[0] == 400
    assert server("/api/settings", {"personas": ["nobody"]})[0] == 400


def test_audio_ranges(server):
    status, data, h = server("/api/recordings/1/audio")
    assert status == 200 and data == b"RIFF....WAVEfmt " and h["Accept-Ranges"] == "bytes"
    status, data, h = server("/api/recordings/1/audio", headers={"Range": "bytes=4-7"})
    assert status == 206 and data == b"...." and h["Content-Range"] == "bytes 4-7/16"
    assert server("/api/recordings/99/audio")[0] == 404
    assert server("/api/nope")[0] == 404


def test_cli_run_and_personas(recorder_db, capsys, monkeypatch):
    monkeypatch.setattr(Coach, "llm", lambda self, s: FakeLLM())
    main(["--db", str(recorder_db), "run"])
    assert "2 recordings analysed" in capsys.readouterr().out
    main(["--db", str(recorder_db), "run", "--recording", "1"])
    assert "recording 1: 1 sentences" in capsys.readouterr().out
    main(["personas"])
    assert "Steve Jobs" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--db", str(recorder_db.parent / "missing.db"), "run"])
