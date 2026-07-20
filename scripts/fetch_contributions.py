#!/usr/bin/env python3
"""Fetch public GitHub contribution data (no token, no GraphQL, no API key).

Scrapes ``https://github.com/users/<username>/contributions`` — the public,
unauthenticated HTML calendar fragment — parses every day cell defensively,
computes summary statistics, and writes ``data/contributions.json`` atomically.

The username is loaded from ``data/profile.json`` (never hardcoded). Genuine
fetch/parse failures exit non-zero and never overwrite previously valid data
with an empty calendar.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "data" / "profile.json"
OUTPUT_PATH = ROOT / "data" / "contributions.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
COUNT_RE = re.compile(r"^\s*(No|[\d,]+)\s+contribution", re.IGNORECASE)


def load_profile(path: Path = PROFILE_PATH) -> dict:
    """Load the shared profile configuration."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Profile config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Profile config is not valid JSON: {exc}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Fetch public GitHub contributions.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args(argv)


def make_session() -> requests.Session:
    """Build a requests session with retries and a realistic User-Agent."""
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def fetch_html(username: str, timeout: float) -> str:
    """Fetch the public contributions HTML fragment."""
    url = f"https://github.com/users/{username}/contributions"
    try:
        response = make_session().get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"Network error fetching {url}: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(f"Unexpected HTTP {response.status_code} from {url}")
    if "ContributionCalendar" not in response.text and "data-date" not in response.text:
        raise RuntimeError("Response did not contain a recognizable contribution calendar.")
    return response.text


def parse_count(text: str | None) -> int | None:
    """Parse a contribution count from accessible text ('No'/'1'/'12 ...')."""
    if not text:
        return None
    match = COUNT_RE.match(text.strip())
    if not match:
        return None
    token = match.group(1)
    if token.lower() == "no":
        return 0
    return int(token.replace(",", ""))


def build_tooltip_map(soup: BeautifulSoup) -> dict[str, str]:
    """Map ``tool-tip[for]`` element ids to their text (modern GitHub markup)."""
    mapping: dict[str, str] = {}
    for tip in soup.find_all("tool-tip"):
        target = tip.get("for")
        if target:
            mapping[target] = tip.get_text(strip=True)
    return mapping


def parse_days(html: str) -> list[dict]:
    """Defensively parse every day cell into {date, count, level} records."""
    soup = BeautifulSoup(html, "html.parser")
    tooltips = build_tooltip_map(soup)

    by_date: dict[str, dict] = {}
    for cell in soup.select("[data-date]"):
        date_str = cell.get("data-date")
        if not date_str:
            continue
        try:
            dt.date.fromisoformat(date_str)
        except ValueError:
            continue  # skip malformed dates.

        # Count: try several sources in order of reliability.
        count = parse_count(cell.get("data-count"))
        if count is None:
            cell_id = cell.get("id")
            if cell_id and cell_id in tooltips:
                count = parse_count(tooltips[cell_id])
        if count is None:
            count = parse_count(cell.get("aria-label"))

        # Level: from data-level, else fall back to count buckets later.
        raw_level = cell.get("data-level")
        try:
            level = int(raw_level) if raw_level is not None else None
        except ValueError:
            level = None

        if count is None:
            count = level if level is not None else 0

        record = {"date": date_str, "count": int(count),
                  "level": level if level is not None else 0}
        # Deduplicate: keep the entry with the higher count.
        existing = by_date.get(date_str)
        if existing is None or record["count"] >= existing["count"]:
            by_date[date_str] = record

    return sorted(by_date.values(), key=lambda d: d["date"])


def deterministic_level(count: int, max_count: int) -> int:
    """Map a count to a 0-5 intensity level using fixed thresholds."""
    if count <= 0 or max_count <= 0:
        return 0
    ratio = count / max_count
    for idx, threshold in enumerate((0.0, 0.15, 0.35, 0.6, 0.85), start=1):
        if ratio <= threshold:
            return max(1, idx - 1)
    return 5


def enrich(days: list[dict]) -> list[dict]:
    """Add weekday/week indexes and normalize levels to the 0-5 scale."""
    if not days:
        return days
    dates = [dt.date.fromisoformat(d["date"]) for d in days]
    first = dates[0]
    # Sunday on or before the first date anchors column 0 (GitHub weeks start Sun).
    base_sunday = first - dt.timedelta(days=(first.weekday() + 1) % 7)
    max_count = max((d["count"] for d in days), default=0)

    enriched: list[dict] = []
    for day, date in zip(days, dates):
        weekday = (date.weekday() + 1) % 7  # Sunday=0 .. Saturday=6
        week = (date - base_sunday).days // 7
        level = day.get("level") or 0
        if not (1 <= level <= 5) and day["count"] > 0:
            level = deterministic_level(day["count"], max_count)
        elif day["count"] == 0:
            level = 0
        enriched.append({
            "date": day["date"],
            "count": day["count"],
            "level": int(level),
            "weekday": weekday,
            "week": week,
        })
    return enriched


def compute_summary(days: list[dict]) -> dict:
    """Compute totals, streaks, best day, active days and monthly totals."""
    counts = {d["date"]: d["count"] for d in days}
    total = sum(counts.values())
    active = [d for d in days if d["count"] > 0]

    best = {"date": None, "count": 0}
    for d in sorted(active, key=lambda x: (-x["count"], x["date"])):
        best = {"date": d["date"], "count": d["count"]}
        break

    monthly: dict[str, int] = {}
    for d in days:
        month = d["date"][:7]
        monthly[month] = monthly.get(month, 0) + d["count"]

    # Longest streak: consecutive calendar days with count > 0.
    longest = streak = 0
    prev: dt.date | None = None
    for d in days:
        date = dt.date.fromisoformat(d["date"])
        if d["count"] > 0:
            if prev is not None and (date - prev).days == 1:
                streak += 1
            else:
                streak = 1
            longest = max(longest, streak)
            prev = date
        else:
            streak = 0
            prev = date

    # Current streak: start today if today has contributions, else yesterday.
    today = dt.date.today()
    anchor = today if counts.get(today.isoformat(), 0) > 0 else today - dt.timedelta(days=1)
    current = 0
    cursor = anchor
    while counts.get(cursor.isoformat(), 0) > 0:
        current += 1
        cursor -= dt.timedelta(days=1)

    active_days = len(active)
    average = round(total / active_days, 2) if active_days else 0.0

    return {
        "total_contributions": total,
        "current_streak": current,
        "longest_streak": longest,
        "best_day": best,
        "active_days": active_days,
        "average_per_active_day": average,
        "monthly_totals": dict(sorted(monthly.items())),
    }


def build_document(username: str, days: list[dict]) -> dict:
    """Assemble the full output document."""
    start = days[0]["date"] if days else None
    end = days[-1]["date"] if days else None
    return {
        "username": username,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "range": {"start": start, "end": end},
        "summary": compute_summary(days),
        "days": days,
    }


def load_existing(path: Path) -> dict | None:
    """Load an existing output document, or None if absent/unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def atomic_write(path: Path, document: dict) -> None:
    """Write JSON atomically via a temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = parse_args(argv)
    try:
        profile = load_profile()
        username = profile.get("github_username")
        if not username:
            raise ValueError("profile.json is missing 'github_username'.")

        print(f"[fetch] Fetching contributions for {username} ...")
        html = fetch_html(username, args.timeout)
        days = enrich(parse_days(html))

        existing = load_existing(args.output)
        if not days:
            if existing and existing.get("days"):
                raise RuntimeError(
                    "Parsed zero contribution days; refusing to overwrite existing data."
                )
            print("[fetch][warn] No contribution data found (empty/private calendar).")

        document = build_document(username, days)

        # Avoid needless commits: if only 'generated_at' would change, keep the
        # previous timestamp so the file bytes stay identical.
        if existing:
            a = {k: v for k, v in existing.items() if k != "generated_at"}
            b = {k: v for k, v in document.items() if k != "generated_at"}
            if a == b:
                document["generated_at"] = existing.get("generated_at", document["generated_at"])
                print("[fetch] No contribution changes; timestamp preserved.")

        atomic_write(args.output, document)
        summary = document["summary"]
        print(f"[fetch] Wrote {args.output} "
              f"({summary['total_contributions']} contributions, "
              f"{len(days)} days, streak {summary['current_streak']}).")
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"[fetch][error] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[fetch][error] Unexpected failure: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
