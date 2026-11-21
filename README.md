# Family Football Pool — results site

Live standings, season standings, and a replay of each week's win-probability
swings, published at <https://mpozar.github.io/fp-results/>.

Fully serverless: a GitHub Actions workflow ([live.yml](.github/workflows/live.yml))
keeps itself alive through game windows, reading the schedule, betting lines,
scores and live win probabilities from ESPN's public scoreboard, and the
pool's picks from a private Google Sheet (via `snapshot_sheet.py`, which
publishes a pick only once it is revealed: at its game's kickoff for early
games, otherwise Sunday 10:00 AM Pacific, when tiebreaker guesses also
appear).

## Scoring (this page is the official scorekeeper)

- Most correct picks wins the week.
- Ties are broken by the tiebreaker: closest to the Monday night game's
  actual total points, by absolute distance (over is not penalized). A
  missing guess counts as 0.
- Entries still tied share the place and split the sum of those places'
  payouts evenly.
- Weekly payouts: $60 / $36 / $20 / $4 from a $120 pot.
- A game's betting line freezes when its picks reveal; before that it tracks
  ESPN's posted (DraftKings) number.
