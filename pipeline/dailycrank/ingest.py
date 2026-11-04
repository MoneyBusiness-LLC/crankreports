"""Collect recent headlines for the Daily Crank question pipeline.

    python pipeline/dailycrank/ingest.py            # print the headlines it finds
    python pipeline/dailycrank/ingest.py --days 3   # only the last 3 days

Uses only the standard library. Feeds live in feeds.json next to this file.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "DailyCrankBot/1.0 (+https://crankreports.com/thedailycrank/)"
PER_FEED = 15
SKIP_WIKI = re.compile(r"^(Main_Page|Special:|Wikipedia:|Portal:|Deaths_in_|Help:|File:)")


def fetch(url: str, timeout: int = 20, tries: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or n == tries - 1:
                raise
            time.sleep(3 * (n + 1))  # Wikipedia rate-limits bursts
    raise RuntimeError("unreachable")


def clean(text: str | None, limit: int = 280) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        d = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if local(c.tag) == name), None)


def text_of(el: ET.Element, name: str) -> str | None:
    c = child(el, name)
    return c.text if c is not None else None


def parse_rss(raw: bytes, feed: dict) -> list[dict]:
    root = ET.fromstring(raw)
    out = []
    items = [e for e in root.iter() if local(e.tag) in ("item", "entry")]
    for it in items[:PER_FEED]:
        link = text_of(it, "link")
        if not link:  # Atom: <link href="..."/>
            l = child(it, "link")
            link = l.get("href") if l is not None else ""
        summary = text_of(it, "description") or text_of(it, "summary") or ""
        if feed["kind"] == "google_trends":  # the trend itself is just a search term; the news item says why
            news = [c for c in it if local(c.tag) == "news_item"]
            if news:
                summary = " / ".join(clean(text_of(n, "news_item_title"), 160) for n in news[:2])
                link = text_of(news[0], "news_item_url") or link
        out.append({
            "source": feed["name"],
            "topic": feed["topic"],
            "title": clean(text_of(it, "title"), 200),
            "summary": clean(summary),
            "link": (link or "").strip(),
            "published": parse_date(text_of(it, "pubDate") or text_of(it, "published") or text_of(it, "updated")),
        })
    return out


def wikipedia_mostread(feed: dict, days: int) -> list[dict]:
    """Wikipedia's most-read articles for the last few days: the best free signal for pop-culture spikes."""
    out, seen = [], set()
    today = datetime.now(timezone.utc).date()
    for back in range(1, min(days, 3) + 1):
        d = today - timedelta(days=back)
        url = feed["url"].format(yyyy=d.year, mm=f"{d.month:02d}", dd=f"{d.day:02d}")
        try:
            data = json.loads(fetch(url))
        except Exception as e:  # noqa: BLE001 - one bad day shouldn't stop the run
            print(f"  ! {feed['name']} {d}: {e}", file=sys.stderr)
            continue
        for a in (data.get("mostread") or {}).get("articles", [])[:25]:
            t = a.get("title", "")
            if t in seen or SKIP_WIKI.match(t):
                continue
            seen.add(t)
            out.append({
                "source": feed["name"],
                "topic": feed["topic"],
                "title": a.get("normalizedtitle") or t.replace("_", " "),
                "summary": clean(f"{a.get('views', 0):,} views on {d}. {a.get('description', '')}. {a.get('extract', '')}"),
                "link": ((a.get("content_urls") or {}).get("desktop") or {}).get("page", ""),
                "published": datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
            })
    return out


def collect(days: int = 7, feeds_path: Path = HERE / "feeds.json") -> list[dict]:
    feeds = json.loads(feeds_path.read_text())["feeds"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items: list[dict] = []
    for f in feeds:
        try:
            got = wikipedia_mostread(f, days) if f["kind"] == "wikipedia_mostread" else parse_rss(fetch(f["url"]), f)
        except Exception as e:  # noqa: BLE001
            print(f"  ! skipped {f['name']}: {e}", file=sys.stderr)
            continue
        got = [g for g in got if g["title"] and (g["published"] is None or g["published"] >= cutoff)]
        print(f"  {len(got):3d}  {f['name']}", file=sys.stderr)
        items += got
    # Drop near-duplicate titles across feeds
    seen, uniq = set(), []
    for it in items:
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq


def as_text(items: list[dict]) -> str:
    """Compact, prompt-friendly listing grouped by topic."""
    by: dict[str, list[dict]] = {}
    for it in items:
        by.setdefault(it["topic"], []).append(it)
    lines = []
    for topic, its in by.items():
        lines.append(f"## {topic}")
        for it in its:
            when = it["published"].strftime("%b %d") if it["published"] else "?"
            s = f" — {it['summary']}" if it["summary"] and it["summary"] != it["title"] else ""
            lines.append(f"- [{when}, {it['source']}] {it['title']}{s} <{it['link']}>")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    items = collect(args.days)
    print(as_text(items))
    print(f"{len(items)} headlines", file=sys.stderr)
