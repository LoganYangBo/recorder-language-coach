# recorder-language-coach

Daily feedback on the English you actually speak, built on
[recorder-sdk](https://github.com/LoganYangBo/recorder-sdk). After every sync it
reads your new recordings (only through the SDK's `Recorder` API), runs a local
model through [Ollama](https://ollama.com), and writes a daily report:

- **Expressions**: your sentences with grammar mistakes, wrong words, unnatural or
  wordy phrasing, a better version, an explanation in Chinese, and how the famous
  people you picked (30 to choose from) might say the same thing.
- **Pronunciation**: words the transcriber wasn't sure about, with the IPA, a tip,
  your own recording and a standard voice.
- **You might have missed**: culture, sports, slang and names other people used
  that you probably didn't get, explained.
- **Corpus**: phrases you save from the report. Search, filter, export to Markdown.

Only your (`me`) English sentences are reviewed; other people's speech is context,
Chinese is skipped. Everything stays on your computer.

## Install

Use its own virtual environment, so it doesn't replace the editable recorder-sdk
you develop in.

```powershell
cd C:\Users\Logan\Projects
git clone https://github.com/LoganYangBo/recorder-language-coach
cd recorder-language-coach
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

Then install Ollama from https://ollama.com/download and pull a model.
For a 12 GB GPU, `qwen3:14b` is the default; `gemma3:12b` is a good second choice.

```powershell
ollama pull qwen3:14b
```

## Use

Point `--db` at the recorder-sdk database (the coach keeps its own `coach.db`
next to it).

```powershell
coach --db C:\Users\Logan\Projects\recorder-sdk\recorder.db ui
```

This opens http://127.0.0.1:8766/ and analyses new recordings every 5 minutes.
The model works in two passes: a short one per recording that only spots
candidates, then one call per day that explains the day's top expressions and
writes the pronunciation tips. It stays loaded during a batch and is unloaded
after it, so Whisper has the GPU back. The console logs how long each call took
and how fast the model ran (tokens/s); if that is slow, run `ollama ps` while it
works: anything other than `100% GPU` means part of the model is on the CPU, and a
smaller model (`qwen3:8b`) or a smaller context in Settings helps.

Other commands:

```text
coach run                      analyse pending recordings now
coach run --day 2026-10-21     analyse a day again (e.g. after changing settings)
coach run --recording 12 13    analyse recordings again
coach watch --interval 300     analyse new recordings in the background, no web page
coach personas                 list the famous people
```

## Settings

On the Settings page: learning goal (work meetings / everyday / exams), famous
people (pick 1–2; each one slows the model down), daily limits (10 expressions and
10 pronunciation tips by default), Ollama model and address, explanation language.

You can add your own famous person, or change a built-in one, by putting a card in
a `personas` folder next to `coach.db`, e.g. `personas/grandma.md`:

```markdown
---
name: Grandma
name_zh: 奶奶
gender: female
tags: [daily]
---
- Warm, short sentences.
- Proverbs.
```

## HTTP API

The page only uses these, so a phone app can too. All JSON.

| Method | Path | |
|---|---|---|
| GET | `/api/status` | what is being analysed, the queue |
| GET | `/api/days` | days with recordings |
| GET | `/api/days/{YYYY-MM-DD}` | the day report |
| POST | `/api/days/{day}/run` | analyse that day again |
| POST | `/api/recordings/{id}/run` | analyse a recording again |
| GET | `/api/recordings/{id}/audio` | the audio, with Range support |
| POST | `/api/expressions/{id}/dismiss` | hide a wrong piece of feedback |
| POST | `/api/known` `{"term"}` | don't explain this term again |
| GET | `/api/corpus?q=&source=` | the corpus |
| POST | `/api/corpus` `{"phrase", "source", ...}` | save a phrase |
| DELETE | `/api/corpus/{id}` | remove a phrase |
| GET | `/api/corpus.md` | the corpus as Markdown |
| GET/POST | `/api/settings` | settings, the famous people, Ollama's models |

## Other languages

v1 is English only, but the language is a setting: prompts live in
`src/coach/prompts/<language>/`, and `coach.lang.DETECTORS` decides which sentences
are in the target language. Adding a language means a prompt folder and a detector.

## Develop

```bash
pip install -e ".[dev]"
pytest -q
ruff check . && ruff format --check .
```

Tests use a fake model; no Ollama needed.
