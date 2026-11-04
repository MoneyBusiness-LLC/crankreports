"""Check Daily Crank days in questions.json against the game's rules.

    python pipeline/dailycrank/validate.py                 # days from yesterday on
    python pipeline/dailycrank/validate.py --day 2026-11-05
    python pipeline/dailycrank/validate.py --all           # every day, including old ones

Errors break the game or its scoring and fail the run. Warnings are worth a look
in review (stale facts, giveaways, repeats) but don't fail it.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "thedailycrank" / "questions.json"
DAY_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COUNTS = {"surface": 5, "midwater": 3}
# Phrases that make a question go stale if the day is replayed later (the game replays
# the latest day on or before today when a day is missing).
STALE = re.compile(r"\b(how old|today|tonight|yesterday|this week|right now|currently|just (?:won|became|announced))\b", re.I)


def norm(s: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def closeness(g: float, a: float) -> float:
    """Same as the game: full points within 5%, none at 2x off."""
    if not g or g <= 0 or a <= 0:
        return 0.0
    r = max(g / a, a / g)
    if r <= 1.05:
        return 1.0
    if r >= 2:
        return 0.0
    return 1 - (math.log(r) - math.log(1.05)) / (math.log(2) - math.log(1.05))


def slider_start(q: dict) -> float:
    """Where the game's slider starts before the player touches it."""
    lo, hi, step = float(q["min"]), float(q["max"]), float(q.get("step") or 1)
    log = bool(q["log"]) if q.get("log") is not None else (lo > 0 and hi / lo >= 1000)
    if not log:
        return round((lo + hi) / 2 / step) * step
    return math.exp((math.log(lo) + math.log(hi)) / 2)


def check_day(key: str, d: dict, history: dict[str, dict] | None = None) -> tuple[list[str], list[str]]:
    errs: list[str] = []
    warns: list[str] = []
    E = lambda m: errs.append(f"{key} {m}")  # noqa: E731
    W = lambda m: warns.append(f"{key} {m}")  # noqa: E731

    if not DAY_KEY.match(key):
        E("key is not YYYY-MM-DD")
    for part in ("surface", "midwater", "bottom"):
        if part not in d:
            E(f"is missing '{part}'")
    if errs:
        return errs, warns

    texts: list[str] = []
    for part, n in COUNTS.items():
        if not isinstance(d[part], list) or len(d[part]) != n:
            E(f"{part}: needs exactly {n} questions, has {len(d[part]) if isinstance(d[part], list) else 'none'}")

    for i, q in enumerate(d["surface"] if isinstance(d["surface"], list) else []):
        tag = f"surface[{i}]"
        for f in ("topic", "question", "answer"):
            if not str(q.get(f, "")).strip():
                E(f"{tag}: '{f}' is empty")
        wrong = q.get("wrong")
        if not isinstance(wrong, list) or len(wrong) != 3 or not all(str(w).strip() for w in wrong):
            E(f"{tag}: 'wrong' needs exactly 3 non-empty choices")
            continue
        opts = [norm(q.get("answer", ""))] + [norm(w) for w in wrong]
        if len(set(opts)) != 4:
            E(f"{tag}: the 4 choices aren't all different")
        a = str(q.get("answer", ""))
        if len(norm(a)) > 3 and norm(a) in norm(q.get("question", "")):
            W(f"{tag}: the answer \"{a}\" appears in the question")
        lens = [len(str(x)) for x in [a, *wrong]]
        if len(a) == max(lens) and max(lens) > 2 * sorted(lens)[1]:
            W(f"{tag}: the answer is much longer than the wrong choices, which gives it away")
        texts.append(q.get("question", ""))

    for i, q in enumerate(d["midwater"] if isinstance(d["midwater"], list) else []):
        tag = f"midwater[{i}]"
        if not str(q.get("question", "")).strip():
            E(f"{tag}: 'question' is empty")
        try:
            a, lo, hi = float(q["answer"]), float(q["min"]), float(q["max"])
        except (KeyError, TypeError, ValueError):
            E(f"{tag}: 'answer', 'min' and 'max' must all be numbers")
            continue
        if any(isinstance(q.get(f), str) for f in ("answer", "min", "max")):
            E(f"{tag}: write 'answer', 'min' and 'max' as numbers, not strings")
        if not lo < hi:
            E(f"{tag}: 'min' must be less than 'max'")
        elif not lo <= a <= hi:
            E(f"{tag}: answer {q['answer']} is outside the slider ({q['min']} to {q['max']})")
        else:
            if a <= 0:
                W(f"{tag}: an answer of 0 or less can never score (scoring is by ratio)")
            start = slider_start(q)
            if closeness(start, a) > 0:
                W(f"{tag}: the slider starts at {start:g}, which already scores {round(300 * closeness(start, a))} of 300 untouched; widen or shift the range")
            span = hi - lo
            if span and (a - lo) / span < 0.03 or span and (hi - a) / span < 0.03:
                W(f"{tag}: the answer sits at the very end of the slider, which gives it away")
        if q.get("step") is not None and not float(q["step"]) > 0:
            E(f"{tag}: 'step' must be greater than 0")
        if not str(q.get("unit", "")).strip():
            W(f"{tag}: no 'unit'")
        texts.append(q.get("question", ""))

    b = d["bottom"] if isinstance(d["bottom"], dict) else {}
    if not str(b.get("clue", "")).strip() or not str(b.get("answer", "")).strip():
        E("bottom: needs a 'clue' and an 'answer'")
    elif re.search(r"\s", str(b["answer"]).strip()):
        E(f"bottom: the answer \"{b['answer']}\" must be one word")
    if not isinstance(b.get("alsoAccept", []), list):
        E("bottom: 'alsoAccept' must be a list")
    texts.append(b.get("clue", ""))

    for t in texts:
        if STALE.search(str(t)):
            W(f"time-sensitive wording, goes stale if this day is replayed: \"{t}\"")

    if history:
        seen = {norm(q.get("question", "")): k for k, day in history.items() if k != key
                for q in [*day.get("surface", []), *day.get("midwater", [])]}
        for t in texts:
            if norm(t) in seen:
                W(f"repeats a question from {seen[norm(t)]}: \"{t}\"")
        if any(k != key and json.dumps(v, sort_keys=True) == json.dumps(d, sort_keys=True) for k, v in history.items()):
            W("is an exact copy of another day")
    return errs, warns


def load(path: Path = QUESTIONS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", action="append", help="check only this day (repeatable)")
    ap.add_argument("--all", action="store_true", help="check every day, including past ones")
    ap.add_argument("--file", type=Path, default=QUESTIONS)
    args = ap.parse_args()

    data = load(args.file)
    days = {k: v for k, v in data.items() if not k.startswith("_")}
    if args.day:
        keys = args.day
    elif args.all:
        keys = sorted(days)
    else:
        since = (date.today() - timedelta(days=1)).isoformat()
        keys = sorted(k for k in days if k >= since)

    all_e, all_w = [], []
    for k in keys:
        if k not in days:
            all_e.append(f"{k} is not in {args.file.name}")
            continue
        e, w = check_day(k, days[k], days)
        all_e += e
        all_w += w
    for m in all_e:
        print(f"ERROR    {m}")
    for m in all_w:
        print(f"warning  {m}")
    print(f"Checked {len(keys)} day(s): {len(all_e)} error(s), {len(all_w)} warning(s).")
    return 1 if all_e else 0


if __name__ == "__main__":
    sys.exit(main())
