"""Add one day to thedailycrank/questions.json after checking it.

    python pipeline/dailycrank/add_day.py 2026-11-05 day.json
    python pipeline/dailycrank/add_day.py 2026-11-05 day.json --replace

day.json holds just that day: {"surface": [...], "midwater": [...], "bottom": {...}}.
Refuses to write if validate.py finds errors; prints warnings either way.
Keeps the file's existing formatting so the diff is only the new day.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate  # noqa: E402


def write_day(path: Path, data: dict, day: str, entry: dict) -> None:
    data[day] = entry
    meta = {k: v for k, v in data.items() if not validate.DAY_KEY.match(k)}
    days = {k: data[k] for k in sorted(k for k in data if validate.DAY_KEY.match(k))}
    path.write_text(json.dumps({**meta, **days}, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day")
    ap.add_argument("entry", type=Path)
    ap.add_argument("--replace", action="store_true", help="overwrite the day if it already exists")
    ap.add_argument("--file", type=Path, default=validate.QUESTIONS)
    args = ap.parse_args()

    data = validate.load(args.file)
    if args.day in data and not args.replace:
        print(f"{args.day} is already in {args.file.name}; use --replace to overwrite it.")
        return 1
    entry = json.loads(args.entry.read_text(encoding="utf-8"))
    errs, warns = validate.check_day(args.day, entry, {k: v for k, v in data.items() if validate.DAY_KEY.match(k)})
    for m in errs:
        print(f"ERROR    {m}")
    for m in warns:
        print(f"warning  {m}")
    if errs:
        print("Not written. Fix the errors above.")
        return 1
    write_day(args.file, data, args.day, entry)
    print(f"Added {args.day} to {args.file} ({len(warns)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
