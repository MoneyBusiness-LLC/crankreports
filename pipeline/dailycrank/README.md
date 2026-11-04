# Daily Crank question pipeline

Every evening at about 6pm Pacific, a GitHub Action drafts the next day's questions from the week's headlines and opens a PR. Approving and merging the PR publishes the day.

```
headlines ──► draft ──► fact-check ──► pick + validate ──► PR ──► you approve ──► live
(ingest.py)   Claude +    a second       validate.py          
              web search  Claude pass
```

1. **Ingest** (`ingest.py`, `feeds.json`): pulls the last 7 days from news, sports, entertainment, music, hip-hop, tech and science feeds, plus Google Trends and Wikipedia's most-read articles. Edit `feeds.json` to add or drop sources.
2. **Draft**: Claude writes 10 surface, 6 mid-water and 3 bottom candidates in the house style (`prompt.md`), using web search to confirm each one. It sees the last 30 days so it won't repeat itself.
3. **Fact-check**: a separate Claude pass checks every candidate from scratch and marks it ok, unsure or reject.
4. **Pick**: takes the best "ok" candidates (falling back to "unsure", which is flagged in the PR), keeps the surface topics varied, and drops anything `validate.py` rejects.
5. **PR**: adds the day to `thedailycrank/questions.json`. The PR description lists each question with its hook, fact-check note and source, any validator warnings, and the leftover candidates as ready-to-paste alternates.

## Day to day

- **Review:** read the PR, swap anything you don't like by editing `questions.json` on the PR branch (alternates are in the description), then approve and merge.
- **Breaking news or a theme:** go to Actions → *Daily Crank questions* → *Run workflow*. Set a theme such as "Election results", pick the date, and tick *replace* to redraft a day that already has a PR. This overwrites edits on that PR branch.
- **Skipped days:** if the day is already in `questions.json` (say you wrote it by hand), the run does nothing.

## Run it locally

```sh
pip install -r pipeline/dailycrank/requirements.txt
export ANTHROPIC_API_KEY=...
python pipeline/dailycrank/ingest.py --days 3                        # just look at headlines (no key needed)
python pipeline/dailycrank/generate.py --dry-run --out /tmp/dc       # draft tomorrow without touching questions.json
python pipeline/dailycrank/generate.py --date 2026-11-05 --theme "Election results"
python pipeline/dailycrank/validate.py                               # check yesterday onward (no key needed)
python pipeline/dailycrank/validate.py --all                         # check every day
```

## What the validator checks

Errors fail the run: wrong question counts, not exactly 3 distinct wrong choices, a slider answer outside min/max, a bottom answer with more than one word, or non-numeric slider values.

Warnings show up in the PR for a human to judge:
- **Untouched slider scores points.** The slider starts in the middle, and tapping *Lock it in* without moving it submits that value. If the middle is within 2× of the answer, a player gets points for nothing.
- The answer sits at the very end of the slider, or appears in the question.
- The right choice is much longer than the wrong ones.
- Time-sensitive wording ("how old", "today", "just became") that goes stale if the day is replayed.
- A question repeated from an earlier day.

## Setup (once)

1. Add an `ANTHROPIC_API_KEY` repository secret (Settings → Secrets and variables → Actions).
2. Turn on Settings → Actions → General → *Allow GitHub Actions to create and approve pull requests*.
3. PRs opened by the Action don't trigger other workflows, so *Daily Crank checks* runs on them only after a person pushes an edit. The generator runs the same checks before it opens the PR.

Cost is roughly $0.50–$1.50 a day: two Opus calls with up to 70 web searches, plus two short formatting calls. The exact figure is at the bottom of each PR.
