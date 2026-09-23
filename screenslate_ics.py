#!/usr/bin/env python3
"""
Generate screenslate.ics — an iCalendar file of upcoming NYC film screenings
from Screen Slate (https://www.screenslate.com).

WHY A NAIVE SCRAPER RETURNS 0 EVENTS
-------------------------------------
Screen Slate's /listings page is a Drupal 10 site whose listings are
rendered client-side. The raw HTML you get back from a plain HTTP request
has no screening data in it at all — so any scraper that parses that page
with BeautifulSoup/regex will reliably find 0 events, no matter how good
the parsing logic is. The page populates itself by calling an internal
JSON API, which is what this script talks to directly:

  1) GET /api/screenings/date?_format=json&date=YYYYMMDD&field_city_target_id=<city id>
     -> a list of {"nid": ..., "field_time": "7:30pm", ...} for that one day

  2) GET /api/screenings/id/<nid1>+<nid2>+...?_format=json
     -> full details for a batch of screening IDs: film title, venue,
        director/year/runtime/format, ticket URL, etc.

There's no public documentation for this API — it's simply what the site's
own frontend calls — so if Screen Slate changes its markup this may need
updating. Run with --verbose if you ever get 0 events again; it'll tell you
whether the problem is upstream (no data came back) or downstream (data
came back but couldn't be parsed).

USAGE
-----
    pip install requests
    python screenslate_ics.py                  # next 7 days, NYC, -> screenslate.ics
    python screenslate_ics.py --days 14
    python screenslate_ics.py --output my.ics --verbose
"""

import argparse
import re
import sys
from datetime import date as date_cls
from datetime import datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo

import requests

SCREENSLATE_BASE = "https://www.screenslate.com"
NYC_CITY_ID = "10969"  # Screen Slate's internal Drupal taxonomy term ID for NYC
UTC = ZoneInfo("UTC")
USER_AGENT = "screenslate-ics/1.0 (+personal calendar sync script)"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def strip_html(s: str) -> str:
    """Remove HTML tags and decode entities."""
    return unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_media_title_info(info_html: str) -> dict:
    """
    Parse the 'media_title_info' field, which holds a sequence of <span>
    elements in order: director (optional), year, runtime, format (optional).
    A leading director span contains a newline before the name; that's how
    Screen Slate's own frontend distinguishes it from the other fields.
    """
    result = {"director": None, "year": None, "runtime": None, "format": None}
    if not info_html:
        return result

    raw_spans = re.findall(r"<span>(.*?)</span>", info_html, re.DOTALL)
    spans = [strip_html(s) for s in raw_spans]
    idx = 0

    if raw_spans and "\n" in raw_spans[0]:
        result["director"] = spans[idx]
        idx += 1
    if idx < len(spans) and re.match(r"^\d{4}$", spans[idx]):
        result["year"] = int(spans[idx])
        idx += 1
    if idx < len(spans) and re.match(r"^\d+M$", spans[idx], re.IGNORECASE):
        result["runtime"] = spans[idx]
        idx += 1
    if idx < len(spans):
        result["format"] = spans[idx]

    return result


def parse_time_to_dt(day: date_cls, time_str: str, tz: ZoneInfo):
    """Combine a date with Screen Slate's 'H:MMam/pm' showtime string into
    a timezone-aware datetime. Returns None if the time can't be parsed
    (e.g. blank or 'TBA'), in which case the caller should fall back to an
    all-day event rather than dropping the screening."""
    if not time_str:
        return None
    for fmt in ("%I:%M%p", "%I:%M %p"):
        try:
            t = datetime.strptime(time_str.strip().lower(), fmt)
            return datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=tz)
        except ValueError:
            continue
    return None


def guess_duration_minutes(runtime_str: str) -> int:
    """Screen Slate's runtime field looks like '95M'. Fall back to a 2-hour
    block (covers trailers/Q&As) when it's missing or unparseable."""
    if runtime_str:
        m = re.match(r"^(\d+)M$", runtime_str.strip(), re.IGNORECASE)
        if m:
            return int(m.group(1))
    return 120


# ---------------------------------------------------------------------------
# Screen Slate API calls
# ---------------------------------------------------------------------------

def fetch_screening_ids(session, days_ahead, city_id, verbose=False):
    """Query the per-day endpoint for each of the next `days_ahead` days.
    Returns a flat list of {"nid", "time", "date"} dicts."""
    today = date_cls.today()
    items = []

    for offset in range(days_ahead):
        day = today + timedelta(days=offset)
        date_str = day.strftime("%Y%m%d")
        url = (
            f"{SCREENSLATE_BASE}/api/screenings/date"
            f"?_format=json&date={date_str}&field_city_target_id={city_id}"
        )
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [warn] {date_str}: request failed ({e})", file=sys.stderr)
            continue

        try:
            data = resp.json()
        except ValueError:
            print(f"  [warn] {date_str}: response wasn't JSON — the API may "
                  f"have changed shape", file=sys.stderr)
            continue

        if verbose:
            print(f"  {date_str}: {len(data)} screening(s)")

        for item in data:
            nid = str(item.get("nid", "")).strip()
            if not nid:
                continue
            items.append({
                "nid": nid,
                "time": item.get("field_time", ""),
                "date": day,
            })

    return items


def fetch_details(session, nids, batch_size=50, verbose=False):
    """Batch-fetch full screening details for a set of nids."""
    details = {}
    nid_list = sorted(set(nids))

    for i in range(0, len(nid_list), batch_size):
        batch = nid_list[i:i + batch_size]
        url = f"{SCREENSLATE_BASE}/api/screenings/id/{'+'.join(batch)}?_format=json"
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  [warn] detail batch starting at {i}: {e}", file=sys.stderr)
            continue

        if verbose:
            print(f"  detail batch {i // batch_size + 1}: {len(data)} record(s)")

        for item in data:
            details[str(item.get("nid"))] = item

    return details


# ---------------------------------------------------------------------------
# ICS generation
# ---------------------------------------------------------------------------

def ics_escape(text: str) -> str:
    """Escape text for an ICS content value (RFC 5545 §3.3.11)."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold_line(line: str, limit: int = 73) -> str:
    """Fold a content line so no line exceeds the RFC 5545 length limit.
    Continuation lines start with a single space."""
    if len(line) <= limit:
        return line
    parts = []
    while len(line) > limit:
        parts.append(line[:limit])
        line = line[limit:]
    parts.append(line)
    return "\r\n ".join(parts)


def build_vevent(occ: dict) -> list:
    """Build the lines of one VEVENT block for a screening occurrence."""
    uid = f"{occ['nid']}-{occ['day'].strftime('%Y%m%d')}@screenslate.ics"
    dtstamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{dtstamp}"]

    if occ["dt"]:
        start_utc = occ["dt"].astimezone(UTC)
        duration = guess_duration_minutes(occ.get("runtime"))
        end_utc = start_utc + timedelta(minutes=duration)
        lines.append(f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}")
    else:
        # No parseable showtime (e.g. "TBA") — fall back to an all-day event
        # rather than silently dropping the screening.
        day_str = occ["day"].strftime("%Y%m%d")
        next_day_str = (occ["day"] + timedelta(days=1)).strftime("%Y%m%d")
        lines.append(f"DTSTART;VALUE=DATE:{day_str}")
        lines.append(f"DTEND;VALUE=DATE:{next_day_str}")

    summary = occ["title"]
    if occ.get("venue"):
        summary += f" — {occ['venue']}"
    lines.append(f"SUMMARY:{ics_escape(summary)}")

    if occ.get("venue"):
        lines.append(f"LOCATION:{ics_escape(occ['venue'])}")

    desc_bits = []
    if occ.get("director"):
        desc_bits.append(f"Dir. {occ['director']}")
    if occ.get("year"):
        desc_bits.append(str(occ["year"]))
    if occ.get("runtime"):
        desc_bits.append(occ["runtime"])
    if occ.get("format"):
        desc_bits.append(occ["format"])
    if occ.get("ticket_url"):
        desc_bits.append(occ["ticket_url"])
    if desc_bits:
        lines.append(f"DESCRIPTION:{ics_escape(' | '.join(desc_bits))}")

    if occ.get("ticket_url"):
        lines.append(f"URL:{occ['ticket_url']}")

    lines.append("END:VEVENT")
    return [fold_line(l) for l in lines]


def write_ics(occurrences: list, path: str) -> None:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//screenslate-ics//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Screen Slate",
    ]
    for occ in occurrences:
        lines.extend(build_vevent(occ))
    lines.append("END:VCALENDAR")

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build screenslate.ics from Screen Slate's listings API."
    )
    parser.add_argument("--days", type=int, default=7,
                         help="Number of days ahead to fetch (default: 7)")
    parser.add_argument("--city-id", default=NYC_CITY_ID,
                         help="Screen Slate city taxonomy ID (default: NYC, 10969)")
    parser.add_argument("--tz", default="America/New_York",
                         help="IANA timezone of the listings (default: America/New_York)")
    parser.add_argument("--output", default="screenslate.ics",
                         help="Output .ics file path (default: screenslate.ics)")
    parser.add_argument("--verbose", action="store_true",
                         help="Print per-day and per-batch counts for debugging")
    args = parser.parse_args()

    tz = ZoneInfo(args.tz)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    print(f"Fetching Screen Slate listings for the next {args.days} day(s)...")
    id_items = fetch_screening_ids(session, args.days, args.city_id, verbose=args.verbose)

    if not id_items:
        print("\nNo screening IDs came back from /api/screenings/date.")
        print("This usually means one of:")
        print("  - field_city_target_id is wrong for what you want (this script")
        print(f"    uses {args.city_id!r}, Screen Slate's NYC term ID)")
        print("  - the date format Screen Slate expects has changed")
        print("  - the API path itself has changed")
        print("Try opening this URL in a browser to see the raw response:")
        today_str = date_cls.today().strftime("%Y%m%d")
        print(f"  {SCREENSLATE_BASE}/api/screenings/date?_format=json"
              f"&date={today_str}&field_city_target_id={args.city_id}")
        write_ics([], args.output)
        print(f"\nSuccess! Generated '{args.output}' with 0 screening events.")
        return

    nids = [it["nid"] for it in id_items]
    unique_count = len(set(nids))
    print(f"Found {unique_count} unique screening ID(s) across {args.days} day(s). "
          f"Fetching details...")
    details = fetch_details(session, nids, verbose=args.verbose)

    if not details:
        print("\nGot screening IDs but no details came back from "
              "/api/screenings/id/... — the detail endpoint may have changed "
              "shape. Re-run with --verbose to see batch-level errors.")
        write_ics([], args.output)
        print(f"\nSuccess! Generated '{args.output}' with 0 screening events.")
        return

    occurrences = []
    skipped_no_detail = 0
    skipped_no_title = 0

    for it in id_items:
        detail = details.get(it["nid"])
        if not detail:
            skipped_no_detail += 1
            continue

        title = strip_html(detail.get("media_title_labels", ""))
        if not title:
            skipped_no_title += 1
            continue

        info = parse_media_title_info(detail.get("media_title_info", ""))
        venue = strip_html(detail.get("venue_title", ""))
        ticket_url = detail.get("field_url", "") or ""
        dt = parse_time_to_dt(it["date"], it["time"], tz)

        occurrences.append({
            "nid": it["nid"],
            "title": title,
            "director": info.get("director"),
            "year": info.get("year"),
            "runtime": info.get("runtime"),
            "format": info.get("format"),
            "venue": venue,
            "ticket_url": ticket_url,
            "day": it["date"],
            "time": it["time"],
            "dt": dt,
        })

    if args.verbose:
        print(f"  skipped (no detail record): {skipped_no_detail}")
        print(f"  skipped (no title): {skipped_no_title}")

    write_ics(occurrences, args.output)
    print(f"\nSuccess! Generated '{args.output}' with {len(occurrences)} screening events.")


if __name__ == "__main__":
    main()
