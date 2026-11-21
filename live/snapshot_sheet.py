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
FROM_ESPN = {"ARI": "AZ"}  # ESPN's alias -> the pool's, where they differ
GOOGLE_EPOCH = datetime(1899, 12, 30)  # sheets serial-number epoch, sheet-local


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
        teams = {c["homeAway"]: FROM_ESPN.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
                 for c in comp["competitors"]}
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


def _sa_session():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    raw = os.environ.get("GOOGLE_SA_KEY", "").strip()
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    if raw.startswith("{"):  # Actions: the secret is the key's content
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=scopes)
    else:  # local: a path to the key file
        path = os.path.expanduser(raw or "~/.config/family-pool-sa.json")
        creds = service_account.Credentials.from_service_account_file(path, scopes=scopes)
    return AuthorizedSession(creds)


def read_sheet_entries(week: int, games: list[dict], all_locked: bool) -> list[dict] | None:
    """[{handle, tb, picks{gid: team}}] from the private sheet — only revealed
    games' picks, tb only after the slate reveal — or None with no sheet.

    Submissions come from the append-only Log tab (one row per submission:
    timestamp, week, username, G1..Gn picks, tiebreaker). Per game, an
    entry's pick is their LAST submission carrying a valid value for it.
    Submission times are NOT checked: keeping an already-started game's pick
    from changing is the submission process's job (per Mike, 2026-11-21),
    not this reader's. The 'Week N' tab provides the roster (all entries,
    pickers or not) and the G-column -> matchup mapping. Nothing read here
    is printed except counts.
    """
    sid = CONFIG.get("sheet_id")
    if not sid:
        return None
    s = _sa_session()
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"
    r = s.get(base + "?fields=properties.timeZone")
    r.raise_for_status()
    tz = ZoneInfo(r.json()["properties"]["timeZone"])

    def values(rng: str, raw: bool = False) -> list[list]:
        q = "?valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=SERIAL_NUMBER" if raw else ""
        resp = s.get(f"{base}/values/{rng}{q}")
        resp.raise_for_status()
        return resp.json().get("values", [])

    # G1..Gn column order is defined by the week tab's matchup header row
    head = values(f"'Week {week}'!A1:Z1")
    matchups = [c for c in (head[0] if head else []) if isinstance(c, str) and "@" in c]
    by_teams = {frozenset((g["road"], g["home"])): g for g in games}
    cols = []
    for m in matchups:
        a, h = (x.strip().upper() for x in m.split("@", 1))
        g = by_teams.get(frozenset((a, h)))
        if g is None:
            raise RuntimeError(f"sheet matchup {m!r} is not on this week's schedule")
        cols.append(g)
    if len(cols) != len(games):
        raise RuntimeError(f"sheet lists {len(cols)} games, the schedule has {len(games)}")
    roster = [str(r0[0]).strip() for r0 in values(f"'Week {week}'!B3:B60") if r0 and str(r0[0]).strip()]

    subs: dict[str, list] = {}
    for row in values("Log!A2:Z5000", raw=True):
        if len(row) < 4 or not isinstance(row[0], (int, float)):
            continue
        try:
            if int(row[1]) != week:
                continue
        except (TypeError, ValueError):
            continue
        ts = (GOOGLE_EPOCH + timedelta(days=float(row[0]))).replace(tzinfo=tz) \
            .astimezone(timezone.utc)
        user = str(row[2]).strip()
        raw_picks = row[3:3 + len(cols)]
        picks = [str(x).strip().upper() or None if isinstance(x, str) else None
                 for x in raw_picks] + [None] * (len(cols) - len(raw_picks))
        tb_cell = row[3 + len(cols)] if len(row) > 3 + len(cols) else None
        tb = int(tb_cell) if isinstance(tb_cell, (int, float)) and not isinstance(tb_cell, bool) else None
        subs.setdefault(user, []).append((ts, picks, tb))

    entries = []
    unknown = [u for u in subs if u not in roster]
    for u in roster + unknown:  # unknown usernames surface instead of vanishing
        rows = sorted(subs.get(u, []), key=lambda x: x[0])
        picks_out = {}
        for ci, g in enumerate(cols):
            if not g["locked"]:
                continue  # not revealed yet; never publish
            team = None
            for ts, pl, _ in rows:
                if pl[ci] in (g["road"], g["home"]):
                    team = pl[ci]
            if team:
                picks_out[g["gid"]] = team
        tb_out = None
        if all_locked:
            for ts, _, tbv in rows:
                if tbv is not None:
                    tb_out = tbv
        entries.append({"handle": u, "tb": tb_out, "picks": picks_out})
    print(f"sheet: {len(roster)} roster entries, {len(subs)} submitted, "
          f"{len(unknown)} unknown usernames, "
          f"{sum(1 for e in entries if e['picks'])} with revealed picks")
    return entries


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
    entries = read_sheet_entries(week, out_games, all_locked)
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
