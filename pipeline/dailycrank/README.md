# Daily Crank question pipeline

At 9:30pm Pacific, the next day's questions are drafted from the latest headlines and opened as a PR. Approving and merging the PR publishes the day; until then, players get the previous day again. There are two ways to run the drafting, and both use the same files here:

| | **Claude Code scheduled task** (current, trial through Nov 11) | **GitHub Action with an API key** (backup) |
|---|---|---|
| Instructions | `RUNBOOK.md` + `prompt.md` | `generate.py` + `prompt.md` |
| When | 9:30pm Pacific daily, in the "Daily Crank Runner" session | By hand: Actions → *Daily Crank questions* → *Run workflow* |
| Billed to | Your Claude plan's usage | `ANTHROPIC_API_KEY` (exact cost shown on each PR) |

The scheduled task's prompt only says "follow `pipeline/dailycrank/RUNBOOK.md`", so the process lives in this repo and changes go through PRs. The schedule itself lives in Claude Code. Each run is a new message in one long-lived session, **Daily Crank Runner**, which has this repo attached with push access; open that session on claude.ai/code to watch a run or see past ones. To pause, extend or retime the schedule, ask Claude in any Claude Code session.

```
headlines ──► draft ──► fact-check ──► pick + validate ──► PR ──► you approve ──► live
(ingest.py)   web       independent    add_day.py,
              search    second pass    validate.py
```

1. **Ingest** (`ingest.py`, `feeds.json`): pulls the last 7 days, with the previous day's news leading, from news, sports, entertainment, music, hip-hop, tech and science feeds, plus Google Trends and Wikipedia's most-read articles. Edit `feeds.json` to add or drop sources.
2. **Draft**: Claude writes 10 surface, 6 mid-water and 3 bottom candidates in the house style (`prompt.md`), using web search to confirm each one. It sees the last 30 days so it won't repeat itself.
3. **Fact-check**: a separate Claude pass checks every candidate from scratch and marks it ok, unsure or reject.
4. **Pick**: takes the best "ok" candidates (falling back to "unsure", which is flagged in the PR), keeps the surface topics varied, and drops anything `validate.py` rejects.
5. **PR**: adds the day to `thedailycrank/questions.json` with `add_day.py`, which refuses days that fail the validator. The PR description lists each question with its hook, fact-check note and source, any validator warnings, and the leftover candidates as ready-to-paste alternates.

## Day to day

- **Review:** read the PR, swap anything you don't like by editing `questions.json` on the PR branch (alternates are in the description), then approve and merge.
- **Breaking news or a theme:** ask Claude for a redraft ("redo Thursday's questions around the election results"), or with an API key, go to Actions → *Daily Crank questions* → *Run workflow*, set a theme, and tick *replace* to redraft a day that already has a PR (this overwrites edits on that PR branch).
- **Skipped days:** if the day is already in `questions.json` (say you wrote it by hand), the run does nothing.

## Run it locally

```sh
pip install -r pipeline/dailycrank/requirements.txt
export ANTHROPIC_API_KEY=...
python pipeline/dailycrank/ingest.py --days 3                        # just look at headlines (no key needed)
python pipeline/dailycrank/generate.py --dry-run --out /tmp/dc       # draft today without touching questions.json
python pipeline/dailycrank/generate.py --date 2026-11-05 --theme "Election results"
python pipeline/dailycrank/validate.py                               # check yesterday onward (no key needed)
python pipeline/dailycrank/validate.py --all                         # check every day
python pipeline/dailycrank/add_day.py 2026-11-05 day.json            # add a hand-written day, after checking it
```

## What the validator checks

Errors fail the run: wrong question counts, not exactly 3 distinct wrong choices, a slider answer outside min/max, a bottom answer with more than one word, or non-numeric slider values.

Warnings show up in the PR for a human to judge:
- **Untouched slider scores points.** The slider starts in the middle, and tapping *Lock it in* without moving it submits that value. If the middle is within 2× of the answer, a player gets points for nothing.
- The answer sits at the very end of the slider, or appears in the question.
- The right choice is much longer than the wrong ones.
- Time-sensitive wording ("how old", "today", "just became") that goes stale if the day is replayed.
- A question repeated from an earlier day.

## Setup

**Scheduled task:** nothing to set up in GitHub. `RUNBOOK.md` must be on `main`; the task reads it from there.

**GitHub Action (only if you use the backup):**
1. Add an `ANTHROPIC_API_KEY` repository secret (Settings → Secrets and variables → Actions).
2. Turn on Settings → Actions → General → *Allow GitHub Actions to create and approve pull requests*.
3. PRs opened by the Action don't trigger other workflows, so *Daily Crank checks* runs on them only after a person pushes an edit. The generator runs the same checks before it opens the PR.

With the API key, cost is roughly $0.50–$1.50 a day (two Opus calls with up to 70 web searches, plus two short formatting calls). The exact figure is at the bottom of each PR.
