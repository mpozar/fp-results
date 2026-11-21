"""Build live/snapshot.json for tick.py: schedule, lines and results context
from ESPN's public scoreboard; everyone's picks and tiebreaker guesses from
the pool's private Google Sheet.

Secrecy contract (same rules the pool has always had): the sheet is private
and this script runs with read access to all of it, but the snapshot it
writes is published in this public repo, so a pick enters the snapshot only
once it is revealed — at its game's kickoff for games that start before the
slate reveal, and otherwise at the slate reveal (Sunday 10:00 AM Pacific).
Tiebreaker guesses reveal with the slate. Nothing read from the sheet is
ever printed: Actions logs on a public repository are public.

Lines freeze at reveal: once a game's picks are visible, its spread (and the
over/under) stop tracking the market, so the published line is a fixed,
auditable fact. Pre-reveal, the latest DraftKings number from ESPN is kept.

The sheet layer is pending the pool's submission process; with no sheet_id
configured this script leaves any existing snapshot untouched.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "live" / "snapshot.json"
CONFIG = json.loads((ROOT / "config.json").read_text())
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


def _iso(ts: str) -> str:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) \
        .astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_week() -> tuple[int, list[dict]]:
    """Current NFL week number and its games from ESPN (id, teams, kickoff,
    spread home-relative — negative means home favored — and the total)."""
    url = SCOREBOARD + os.environ.get("ESPN_QUERY", "")  # test hook
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    j = r.json()
    week = j["week"]["number"]
    games = []
    for ev in j.get("events", []):
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c["team"]["abbreviation"] for c in comp["competitors"]}
        odds = (comp.get("odds") or [{}])[0]
        games.append({"gid": str(ev["id"]), "road": teams["away"], "home": teams["home"],
                      "startsAt": _iso(ev["date"]),
                      "line": odds.get("spread"), "total": odds.get("overUnder")})
    games.sort(key=lambda g: g["startsAt"])
    return week, games


def slate_reveal(games: list[dict]) -> datetime:
    """The moment every pick and tiebreaker guess becomes public: 10:00 AM
    Pacific on the week's main game day (the day, in Pacific time, with the
    most kickoffs — Sunday in a normal week; robust to holiday oddities)."""
    tz = ZoneInfo(CONFIG["reveal"]["timezone"])
    hh, mm = (int(x) for x in CONFIG["reveal"]["time"].split(":"))
    days: dict = {}
    for g in games:
        d = datetime.fromisoformat(g["startsAt"].replace("Z", "+00:00")).astimezone(tz).date()
        days[d] = days.get(d, 0) + 1
    main_day = max(days, key=lambda d: (days[d], d))
    return datetime(main_day.year, main_day.month, main_day.day, hh, mm, tzinfo=tz) \
        .astimezone(timezone.utc)


def read_sheet_entries(revealed: set[str], all_revealed: bool) -> list[dict] | None:
    """[{handle, tb, picks{gid: team}}] with ONLY revealed games' picks, and
    tb only after the slate reveal — or None when no sheet is configured.

    PENDING: the pool's submission sheet doesn't exist yet. When it does,
    this reads it with the service-account key in the GOOGLE_SA_KEY secret,
    keeps each entry's last pre-deadline submission, and filters as above.
    """
    if not CONFIG.get("sheet_id"):
        return None
    raise NotImplementedError("sheet format not wired up yet")


def main() -> None:
    week, games = fetch_week()
    now = datetime.now(timezone.utc)
    reveal_all = slate_reveal(games)
    old = {}
    if SNAP.exists():
        old = json.loads(SNAP.read_text())
    frozen = {g["gid"]: g for g in old.get("games", [])} if old.get("week") == week else {}

    out_games = []
    for g in games:
        kick = datetime.fromisoformat(g["startsAt"].replace("Z", "+00:00"))
        reveal = min(kick, reveal_all)
        locked = now >= reveal
        f = frozen.get(g["gid"])
        if f and f.get("locked") and f.get("line") is not None:
            line, total = f["line"], f.get("total")  # frozen at reveal, keep
        else:
            line, total = g["line"], g["total"]
        out_games.append({"gid": g["gid"], "road": g["road"], "home": g["home"],
                          "line": line if line is not None else 0.0, "total": total,
                          "startsAt": g["startsAt"],
                          "lockAt": reveal.isoformat(timespec="seconds").replace("+00:00", "Z"),
                          "locked": locked})

    all_locked = now >= reveal_all
    entries = read_sheet_entries({g["gid"] for g in out_games if g["locked"]}, all_locked)
    if entries is None:
        print("no sheet configured; snapshot left untouched")
        return
    tb_gid = out_games[-1]["gid"] if out_games else None  # latest kickoff = MNF
    snap = {"week": week,
            "generated": now.isoformat(timespec="seconds"),
            "pot": CONFIG["pot"], "payouts": [float(p) for p in CONFIG["payouts"]],
            "all_locked": all_locked, "tb_gid": tb_gid,
            "games": out_games, "entries": entries}
    if old and ({k: v for k, v in snap.items() if k != "generated"} ==
                {k: v for k, v in old.items() if k != "generated"}):
        print("snapshot unchanged")
        return
    SNAP.write_text(json.dumps(snap))
    n = sum(1 for e in entries if e["picks"])
    print(f"wrote snapshot: week {week}, {len(out_games)} games, "
          f"picks revealed for {n} entries, all_locked={all_locked}")


if __name__ == "__main__":
    main()
