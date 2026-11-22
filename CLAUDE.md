# fp-results: context for agents working in this repo

Public results site for the Family Football Pool, published by GitHub Pages at
https://mpozar.github.io/fp-results/. Any push to `main` deploys in about a
minute. This page is the pool's official scorekeeper, so correctness and the
secrecy rules below outrank everything else.

## Layout

| Path | What it is | Edit? |
|---|---|---|
| `index.html` | Weekly standings page (nav, submit-picks link, pick grid) | yes |
| `season.html` | Season standings | yes |
| `odds.html` | Live win-probability chart + replay | yes |
| `config.json` | Pot, payouts, sheet id, reveal time, `deadline_exempt` | yes |
| `README.md` | Human-facing docs, scoring rules | yes |
| `live/snapshot_sheet.py` | Builds `live/snapshot.json` from ESPN + the private picks sheet | carefully |
| `live/tick.py` | Builds `data.json` / settles weeks from the snapshot | carefully |
| `.github/workflows/live.yml` | Self-sustaining updater (see below) | carefully |
| `data.json`, `state.json`, `live/snapshot.json`, `history_N.json` | Machine-written by the updater | never by hand |
| `week_N.json` | Settled weeks: the official record, wins and payouts | never |

## Common small tasks

- **Change the submit-picks form link**: it is the `submit picks ↗` anchor in
  `index.html`'s nav. Edit the `href`, commit, push. Nothing else needed.
- **Pot or payout change**: `config.json` (`pot`, `payouts`). Takes effect on
  the next snapshot; already-settled `week_N.json` files are not recomputed.
- **Accept a late pick for a started game**: add the matchup to
  `config.json` → `deadline_exempt`, e.g. `{"12": ["GB@DET"]}`. This waives
  the per-game submission deadline for that matchup that week only.
- **Styling/text changes to the pages**: plain HTML/JS, no build step.

## How the data flows (don't fight it)

1. A GitHub Actions run (`live.yml`) loops during game windows. Each loop:
   `snapshot_sheet.py` reads ESPN (schedule, lines, scores, win probability)
   and the pool's **private Google Sheet** (everyone's picks), then
   `tick.py` renders `data.json` and, after Monday night, settles the week
   into `week_N.json`.
2. Runs re-dispatch themselves and sleep precisely toward the next kickoff
   or reveal; crons are only a backup. You should never need to babysit it.
   To force an update now: `gh workflow run live.yml -f chain=1`.
3. The bot commits as `pool-live-bot`. **Always `git pull --rebase
   --autostash` before you commit**, and expect push races during games.

## Picks sheet (private)

- Google Sheet id is in `config.json`. The `Log` tab is append-only: one row
  per submission (timestamp, week, username, G1..Gn picks, tiebreaker). Per
  game, an entry's pick is their latest valid submission before that game's
  reveal. `Week N` tabs define the roster and the game column order.
- The sheet is read in Actions through the `GOOGLE_SA_KEY` repo secret (a
  Google service account). There is no key in the repo and none should ever
  be committed.

## Hard rules

1. **Secrecy**: a pick becomes public at its game's kickoff, everything
   (including tiebreakers) at Sunday 10:00 AM Pacific. Only
   `snapshot_sheet.py` enforces this. Never print sheet contents (picks,
   tiebreakers, timestamps per person) in workflow logs; logs on a public
   repo are public. Print counts only. Never commit unrevealed picks in any
   file.
2. **Never edit settled `week_N.json` files** or hand-edit the
   machine-written files; a later run would fight you, and the archives are
   the money record.
3. **No credentials in the repo**, ever: no service-account JSON, no tokens.
4. **Scoring rules live in README.md** and in `tick.py`; don't change either
   without the pool agreeing first.
5. If a change touches `live/` or the workflow, test locally before pushing:
   `python live/snapshot_sheet.py && python live/tick.py` (needs
   `GOOGLE_SA_KEY` set to a key-file path; without it the snapshot step is
   skipped and tick runs off the committed snapshot).

Questions: Mike (repo owner, @mpozar).
