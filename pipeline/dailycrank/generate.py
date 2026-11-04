"""Draft one Daily Crank day from the latest headlines.

    python pipeline/dailycrank/generate.py                         # today (Pacific time)
    python pipeline/dailycrank/generate.py --date 2026-11-05 --theme "Election results"
    python pipeline/dailycrank/generate.py --dry-run --out /tmp/dc  # don't touch questions.json

Steps:
  1. ingest   pull headlines (ingest.py, feeds.json)
  2. draft    Claude with web search writes more candidates than a day needs, with sources
  3. check    a second Claude pass with web search fact-checks every candidate on its own
  4. pick     keep the best candidates that pass the fact-check and validate.py
  5. write    add the day to thedailycrank/questions.json, and write pr_body.md + candidates.json to --out

Needs ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

import anthropic
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest  # noqa: E402
import validate  # noqa: E402
from add_day import write_day  # noqa: E402

HERE = Path(__file__).resolve().parent
MODEL = os.environ.get("DC_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
NEED = {"surface": 5, "midwater": 3, "bottom": 1}
DRAFT = {"surface": 10, "midwater": 6, "bottom": 3}
HISTORY_DAYS = 30
PT = ZoneInfo("America/Los_Angeles")

client = anthropic.Anthropic()
invalid: dict[str, str] = {}  # candidate id -> why validate.py rejected it
usage = {"input": 0, "output": 0, "cache_read": 0, "searches": 0, "calls": 0}


# ---------- shapes ----------
class Surface(BaseModel):
    id: str
    topic: str
    question: str
    answer: str
    wrong: list[str]
    hook: str
    source_url: str


class Midwater(BaseModel):
    id: str
    question: str
    answer: float
    unit: str
    min: float
    max: float
    step: Optional[float]
    hook: str
    source_url: str


class Bottom(BaseModel):
    id: str
    clue: str
    answer: str
    alsoAccept: list[str]
    hook: str
    source_url: str


class Candidates(BaseModel):
    surface: list[Surface]
    midwater: list[Midwater]
    bottom: list[Bottom]


class Verdict(BaseModel):
    id: str
    verdict: Literal["ok", "unsure", "reject"]
    note: str
    source_url: str


class Verdicts(BaseModel):
    verdicts: list[Verdict]


# ---------- Claude calls ----------
def _count(u) -> None:
    usage["calls"] += 1
    usage["input"] += u.input_tokens or 0
    usage["output"] += u.output_tokens or 0
    usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
    stu = getattr(u, "server_tool_use", None)
    usage["searches"] += (getattr(stu, "web_search_requests", 0) or 0) if stu else 0


def research(system: str, prompt: str, max_searches: int) -> str:
    """One Claude turn with web search. Returns the reply's text."""
    messages: list[dict] = [{"role": "user", "content": prompt}]
    for _ in range(8):  # resume server-side pauses (pause_turn) a few times at most
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=system,
            messages=messages,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}],
            output_config={"effort": "high"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
        _count(msg.usage)
        blocks = [b.to_dict() for b in msg.content]
        if messages[-1]["role"] == "assistant":
            messages[-1]["content"] += blocks
        else:
            messages.append({"role": "assistant", "content": blocks})
        if msg.stop_reason == "pause_turn":
            continue
        if msg.stop_reason == "refusal":
            raise SystemExit(f"Claude declined the request: {getattr(msg, 'stop_details', None)}")
        if msg.stop_reason == "max_tokens":
            raise SystemExit("Claude ran out of output tokens; try again or lower the candidate counts.")
        return "".join(b.get("text", "") for b in messages[-1]["content"] if b.get("type") == "text").strip()
    raise SystemExit("Claude kept pausing; giving up.")


def structure(text: str, shape: type[BaseModel], what: str) -> BaseModel:
    """Turn a free-text reply into validated JSON (no tools, so structured output is safe)."""
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "low"},
        output_format=shape,
        messages=[{"role": "user", "content": f"Convert these {what} into the JSON schema exactly. Copy wording, numbers and URLs as written; don't add, drop or change anything.\n\n{text}"}],
    )
    _count(resp.usage)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise SystemExit(f"Couldn't structure the {what} (stop_reason={resp.stop_reason}).")
    return resp.parsed_output


# ---------- steps ----------
def recent_history(data: dict, day: str) -> str:
    keys = sorted(k for k in data if validate.DAY_KEY.match(k) and k < day)[-HISTORY_DAYS:]
    lines = []
    for k in keys:
        d = data[k]
        qs = [f"{q['question']} → {q['answer']}" for q in d.get("surface", []) + d.get("midwater", [])]
        qs.append(f"{d['bottom']['clue']} → {d['bottom']['answer']}")
        lines.append(f"{k}: " + " | ".join(qs))
    return "\n".join(lines) or "(none yet)"


def draft(day: str, theme: str | None, headlines: str, history: str) -> tuple[Candidates, str]:
    weekday = datetime.fromisoformat(day).strftime("%A, %B %-d, %Y")
    prompt = f"""Write candidate questions for the Daily Crank on {weekday}. It'll be played all that day, so write it as of that date. Lead with what happened yesterday, the day before; use the rest of the week to fill gaps.

{f"Editor's theme for this day: {theme}. Lean into it, but keep the day varied enough to be fun." if theme else "No theme set: build a varied day from the strongest hooks below."}

Write {DRAFT['surface']} surface, {DRAFT['midwater']} mid-water and {DRAFT['bottom']} bottom candidates, so the editor can swap some out. List each group best-first. Give every candidate a short id (s1, s2…, m1…, b1…), the headline or reason that inspired it (its hook), and one source URL that confirms the answer.

<headlines>
{headlines}
</headlines>

<recent_days>
Questions already used recently. Don't repeat their questions, answers or main subjects.
{history}
</recent_days>"""
    system = (HERE / "prompt.md").read_text()
    text = research(system, prompt, max_searches=30)
    return structure(text, Candidates, "trivia question candidates"), text


def fact_check(day: str, cands: Candidates) -> dict[str, Verdict]:
    items = cands.model_dump()
    for group in items.values():
        for q in group:
            q.pop("hook", None)
    prompt = f"""You're the fact-checker for a daily trivia game. These questions go live on {day} for thousands of players, and a wrong answer is embarrassing. Check every candidate independently, using web search. Don't trust the given source_url; find your own confirmation.

For each candidate, check:
- The answer is correct as of {day}, and it's the only defensible answer to the question as worded.
- Surface: each of the 3 wrong choices is definitely wrong.
- Mid-water: the answer matches what the question measures (units, scope, date), within about 2% of the best source, and lies between min and max.
- Bottom: both halves of the clue fit the one-word answer, and alsoAccept holds no wrong answers.
- Nothing is still developing, disputed, or likely to change before the day is played.

Give each id a verdict: ok (confirmed), unsure (couldn't confirm, or it's ambiguous) or reject (wrong, or a wrong choice is also right). Add a one-line note saying what you found and the URL you relied on.

<candidates>
{json.dumps(items, indent=2, ensure_ascii=False)}
</candidates>"""
    text = research("You are a meticulous, skeptical trivia fact-checker.", prompt, max_searches=40)
    v = structure(text, Verdicts, "fact-check verdicts")
    return {x.id: x for x in v.verdicts}


def num(x: float | None):
    if x is None:
        return None
    return int(x) if float(x).is_integer() else x


def to_game(part: str, c) -> dict:
    if part == "surface":
        return {"topic": c.topic, "question": c.question, "answer": c.answer, "wrong": c.wrong}
    if part == "midwater":
        q = {"question": c.question, "answer": num(c.answer), "unit": c.unit, "min": num(c.min), "max": num(c.max)}
        if c.step:
            q["step"] = num(c.step)
        return q
    return {"clue": c.clue, "answer": c.answer, "alsoAccept": c.alsoAccept}


def pick(day: str, cands: Candidates, verdicts: dict[str, Verdict], themed: bool, data: dict):
    """Best-first selection: fact-checked 'ok' first, then 'unsure' (flagged), skipping anything the validator rejects."""
    chosen: dict[str, list] = {}
    for part, need in NEED.items():
        pool = getattr(cands, part)
        ranked = [c for c in pool if verdicts.get(c.id) and verdicts[c.id].verdict == "ok"] + \
                 [c for c in pool if verdicts.get(c.id) and verdicts[c.id].verdict == "unsure"]
        out, topics = [], {}
        for c in ranked:
            q = to_game(part, c)
            e, _ = validate.check_day(day, {
                "surface": [q] * 5 if part == "surface" else [],
                "midwater": [q] * 3 if part == "midwater" else [],
                "bottom": q if part == "bottom" else {"clue": "x", "answer": "x"},
            })
            # The placeholder day leaves the other rounds empty, so ignore their question-count errors
            e = [m for m in e if not re.search(r"^\S+ (surface|midwater): needs exactly", m)]
            if e:
                invalid[c.id] = e[0].split(" ", 1)[1]
                print(f"  skipping {c.id}: {invalid[c.id]}", file=sys.stderr)
                continue
            if len(out) == need or (part == "surface" and not themed and topics.get(c.topic.lower(), 0) >= 2):
                continue
            out.append(c)
            if part == "surface":
                topics[c.topic.lower()] = topics.get(c.topic.lower(), 0) + 1
        if len(out) < need:
            raise SystemExit(f"Only {len(out)} usable {part} candidates after fact-checking; need {need}. Re-run, or add a theme.")
        chosen[part] = out
    entry = {
        "surface": [to_game("surface", c) for c in chosen["surface"]],
        "midwater": [to_game("midwater", c) for c in chosen["midwater"]],
        "bottom": to_game("bottom", chosen["bottom"][0]),
    }
    hist = {k: v for k, v in data.items() if validate.DAY_KEY.match(k)}
    errs, warns = validate.check_day(day, entry, hist)
    if errs:
        raise SystemExit("The assembled day fails validation:\n" + "\n".join(errs))
    return entry, chosen, warns


# ---------- PR body ----------
def md(s: object) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def link(url: str) -> str:
    return f"[source]({url})" if url.startswith("http") else md(url or "–")


def badge(v: Verdict | None) -> str:
    return {"ok": "✅", "unsure": "⚠️ unsure", "reject": "❌"}.get(v.verdict if v else "", "?")


def pr_body(day: str, theme: str | None, cands: Candidates, verdicts: dict[str, Verdict], chosen: dict, warns: list[str], n_headlines: int) -> str:
    when = datetime.fromisoformat(day).strftime("%a %b %-d")
    used = {c.id for cs in chosen.values() for c in cs}
    L = [f"Drafted automatically from {n_headlines} recent headlines" + (f", themed **{md(theme)}**" if theme else "") +
         f". **Approving and merging publishes {when}.** To swap a question, edit `thedailycrank/questions.json` on this branch; alternates are below.", ""]

    L += ["### Surface", "| | Topic | Question | Answer | Wrong | Why | Check |", "|-|-|-|-|-|-|-|"]
    for i, c in enumerate(chosen["surface"], 1):
        v = verdicts.get(c.id)
        L.append(f"| {i} | {md(c.topic)} | {md(c.question)} | **{md(c.answer)}** | {md(', '.join(c.wrong))} | {md(c.hook)} | {badge(v)} {md(v.note if v else '')} {link(v.source_url if v else c.source_url)} |")
    L += ["", "### Mid-water", "| | Question | Answer | Slider | Why | Check |", "|-|-|-|-|-|-|"]
    for i, c in enumerate(chosen["midwater"], 1):
        v = verdicts.get(c.id)
        L.append(f"| {i} | {md(c.question)} | **{num(c.answer):,} {md(c.unit)}** | {num(c.min):,}–{num(c.max):,} | {md(c.hook)} | {badge(v)} {md(v.note if v else '')} {link(v.source_url if v else c.source_url)} |")
    b = chosen["bottom"][0]
    v = verdicts.get(b.id)
    L += ["", "### Bottom", f"> {md(b.clue)}", "",
          f"**{md(b.answer)}**" + (f" (also accepts {md(', '.join(b.alsoAccept))})" if b.alsoAccept else "") +
          f" · {md(b.hook)} · {badge(v)} {md(v.note if v else '')} {link(v.source_url if v else b.source_url)}", ""]

    if warns:
        L += ["### Validator warnings", *[f"- {md(w)}" for w in warns], ""]

    alts = [(p, c) for p in NEED for c in getattr(cands, p) if c.id not in used and c.id not in invalid and verdicts.get(c.id) and verdicts[c.id].verdict != "reject"]
    if alts:
        L += ["<details><summary><b>Alternates</b> (" + str(len(alts)) + ", paste over a question to swap)</summary>", ""]
        for p, c in alts:
            v = verdicts[c.id]
            L += [f"**{p} · {c.id}** {badge(v)} {md(v.note)} {link(v.source_url)}", "```json", json.dumps(to_game(p, c), ensure_ascii=False), "```"]
        L += ["</details>", ""]
    why = lambda c: invalid.get(c.id) or (verdicts[c.id].note if c.id in verdicts else "not fact-checked")  # noqa: E731
    rej = [c for p in NEED for c in getattr(cands, p) if c.id in invalid or not verdicts.get(c.id) or verdicts[c.id].verdict == "reject"]
    if rej:
        L += ["<details><summary><b>Rejected</b> (" + str(len(rej)) + ", by the fact-checker or the validator)</summary>", ""]
        L += [f"- **{c.id}** {md(getattr(c, 'question', None) or c.clue)} → {md(c.answer)}: {md(why(c))}" for c in rej]
        L += ["</details>", ""]

    cost = usage["input"] / 1e6 * 4 + usage["output"] / 1e6 * 20 + usage["searches"] / 1000 * 10
    L += ["---", f"<sub>{MODEL} · {usage['calls']} calls · {usage['input']:,} in / {usage['output']:,} out tokens · {usage['searches']} web searches · about ${cost:.2f}</sub>"]
    return "\n".join(L)


# ---------- main ----------
def gh_output(**kv) -> None:
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="day to write, YYYY-MM-DD (default: today, Pacific time)")
    ap.add_argument("--theme", help="optional editor's theme, e.g. 'Election results' or 'Halloween'")
    ap.add_argument("--replace", action="store_true", help="overwrite the day if it already exists")
    ap.add_argument("--dry-run", action="store_true", help="don't modify questions.json")
    ap.add_argument("--days", type=int, default=7, help="how many days of headlines to use")
    ap.add_argument("--file", type=Path, default=validate.QUESTIONS)
    ap.add_argument("--out", type=Path, default=Path("dailycrank-out"), help="where to write pr_body.md and candidates.json")
    args = ap.parse_args()

    day = args.date or datetime.now(PT).date().isoformat()
    date.fromisoformat(day)
    theme = (args.theme or "").strip() or None
    data = validate.load(args.file)
    gh_output(day=day)
    if day in data and not args.replace:
        print(f"{day} already exists in {args.file.name}; nothing to do (use --replace to redo it).")
        gh_output(skip="true")
        return 0

    print(f"Daily Crank: drafting {day}" + (f" (theme: {theme})" if theme else ""), file=sys.stderr)
    print("1/4 headlines", file=sys.stderr)
    items = ingest.collect(args.days)
    print(f"2/4 drafting from {len(items)} headlines", file=sys.stderr)
    cands, draft_text = draft(day, theme, ingest.as_text(items), recent_history(data, day))
    print(f"    {len(cands.surface)} surface, {len(cands.midwater)} mid-water, {len(cands.bottom)} bottom", file=sys.stderr)
    print("3/4 fact-checking", file=sys.stderr)
    verdicts = fact_check(day, cands)
    tally = {k: sum(v.verdict == k for v in verdicts.values()) for k in ("ok", "unsure", "reject")}
    print(f"    {tally}", file=sys.stderr)
    print("4/4 picking", file=sys.stderr)
    entry, chosen, warns = pick(day, cands, verdicts, bool(theme), data)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "candidates.json").write_text(json.dumps({
        "day": day, "theme": theme, "entry": entry, "candidates": cands.model_dump(),
        "verdicts": {k: v.model_dump() for k, v in verdicts.items()}, "usage": usage, "draft_text": draft_text,
    }, indent=2, ensure_ascii=False))
    (args.out / "pr_body.md").write_text(pr_body(day, theme, cands, verdicts, chosen, warns, len(items)))
    if not args.dry_run:
        write_day(args.file, data, day, entry)
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    print(f"Wrote {args.out}/pr_body.md" + ("" if args.dry_run else f" and added {day} to {args.file}"), file=sys.stderr)
    gh_output(skip="false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
