import datetime
import re
from bs4 import BeautifulSoup
from icalendar import Calendar, Event
import pytz
import requests

URL = "https://www.screenslate.com/listings"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.screenslate.com/",
}

# General UI / Navigation links to ignore
IGNORED_TITLE_WORDS = {
    "screen slate", "screenslate", "listings", "articles", "about",
    "donate", "search", "home", "instagram", "twitter", "facebook",
    "log in", "sign up", "previous day", "next day", "screenings",
    "exhibitions", "calendar", "archive", "read more", "view details",
    "membership", "support", "contact", "editorial", "rss", "terms", "privacy"
}

# Flexible regex for showtimes: e.g. 7:00 PM, 7:00pm, 7pm, 7:00, 12:30p
TIME_REGEX = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:[aApP]\.?[mM]\.?|[aApP]|am|pm)?)\b"
)


def parse_time(time_str):
    """Parse time strings (e.g., '7:00 PM', '7:00pm', '7pm', '19:30', '7:30') into datetime.time."""
    cleaned = time_str.strip().lower().replace(".", "").replace(" ", "")
    if not cleaned:
        return None

    # Handle HH:MM without explicit AM/PM (default evening times < 12 to PM)
    if re.match(r"^\d{1,2}:\d{2}$", cleaned):
        parts = cleaned.split(":")
        hr, mn = int(parts[0]), int(parts[1])
        if 1 <= hr <= 11:
            hr += 12
        return datetime.time(hr, mn)

    # Convert formats like '7pm' -> '7:00pm' or '7p' -> '7:00pm'
    cleaned = re.sub(r"^(\d{1,2})(am|pm|a|p)$", r"\1:00\2", cleaned)
    cleaned = re.sub(r"([ap])$", r"\1m", cleaned)

    for fmt in ("%I:%M%p", "%H:%M", "%I%p"):
        try:
            return datetime.datetime.strptime(cleaned, fmt).time()
        except ValueError:
            pass
    return None


def extract_runtime(text):
    """Parses runtime in minutes from listing text (e.g. '112 min', '1h 45m', '95m').
    Defaults to 120 minutes (2 hours) if omitted.
    """
    # Match '1h 45m' or '2 hrs 10 mins'
    h_m = re.search(
        r"\b(\d+)\s*h(?:our|ours|r)?\s*(?:(\d+)\s*m(?:in|ins|inute|inutes)?)?\b",
        text,
        re.IGNORECASE,
    )
    if h_m:
        hours = int(h_m.group(1))
        mins = int(h_m.group(2)) if h_m.group(2) else 0
        if hours < 8:
            return (hours * 60) + mins

    # Match '112 min', '95m', '105 mins'
    m_only = re.search(
        r"\b(\d{2,3})\s*(?:min|mins|m|minutes)\b", text, re.IGNORECASE
    )
    if m_only:
        return int(m_only.group(1))

    return 120  # Default fallback: 2 hours


def find_screening_container(a_tag):
    """Scans up parent levels from a link tag to find the smallest container with showtimes."""
    curr = a_tag.parent
    depth = 0
    while curr and depth < 5 and curr.name not in ["html", "body"]:
        text = curr.get_text(" ", strip=True)
        # Look for explicit time formats with AM/PM or colon time strings
        raw_matches = re.findall(
            r"\b(\d{1,2}:\d{2}\s*(?:[aApP]\.?[mM]\.?|[aApP]|am|pm)?|\d{1,2}\s*(?:[aApP]\.?[mM]\.?|[aApP]))\b",
            text,
        )
        if raw_matches:
            valid_times = []
            for t in raw_matches:
                p = parse_time(t)
                if p is not None:
                    valid_times.append(t)
            if valid_times:
                return curr, list(set(valid_times))
        curr = curr.parent
        depth += 1
    return None, []


def get_venue_for_listing(a_tag):
    """Finds the associated venue title by scanning backward in the DOM."""
    for prev in a_tag.find_all_previous(
        ["h1", "h2", "h3", "h4", "h5", "div", "p", "strong", "b"]
    ):
        text = prev.get_text(" ", strip=True)
        if not text or len(text) < 3 or len(text) > 90:
            continue

        # Skip elements that contain showtimes (movie cards, not venue headers)
        if re.search(r"\b\d{1,2}:\d{2}\b", text):
            continue

        low = text.lower()
        if any(
            term in low
            for term in [
                "screenings", "exhibitions", "screen slate", "donate",
                "subscribe", "newsletter", "search", "menu", "articles"
            ]
        ):
            continue

        is_heading = prev.name in ["h1", "h2", "h3", "h4", "h5"]
        is_uppercase = text.isupper() and len(text) > 4
        has_venue_class = any(
            "venue" in c.lower() or "location" in c.lower() or "cinema" in c.lower()
            for c in prev.get("class", [])
        )

        if is_heading or is_uppercase or has_venue_class:
            return text.title() if text.isupper() else text

    return "Screen Slate Venue"


def scrape_and_build_ics():
    print(f"Fetching {URL}...")
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        res = session.get(URL, timeout=15)
        res.raise_for_status()
        print(f"Successfully fetched page (HTTP {res.status_code}, {len(res.text)} bytes).")
    except Exception as e:
        print(f"Failed to fetch {URL}: {e}")
        return

    soup = BeautifulSoup(res.text, "html.parser")

    # Strip script/style tags only (do NOT decompose structural body divs)
    for tag in soup(["script", "style"]):
        tag.decompose()

    tz = pytz.timezone("America/New_York")
    today = datetime.datetime.now(tz).date()

    cal = Calendar()
    cal.add("prodid", "-//Screen Slate NYC Screenings//screenslate.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Screen Slate Screenings")

    events_added = 0
    seen_events = set()
    sample_events = []

    # Find all hyperlink candidate nodes
    all_links = soup.find_all("a", href=True)
    print(f"Scanning {len(all_links)} candidate links for movie screening listings...")

    for a_tag in all_links:
        movie_title = a_tag.get_text(strip=True)
        href = a_tag["href"]

        if not movie_title or len(movie_title) < 2:
            continue
        if movie_title.lower() in IGNORED_TITLE_WORDS:
            continue

        # Check if this link is situated inside the EXHIBITIONS section
        is_in_exhibitions = False
        for prev in a_tag.find_all_previous(["h1", "h2", "h3", "h4", "h5"]):
            if "EXHIBITIONS" in prev.get_text(strip=True).upper():
                is_in_exhibitions = True
                break
        if is_in_exhibitions:
            continue

        # Find container element with showtimes
        container, time_strings = find_screening_container(a_tag)
        if not time_strings or not container:
            continue

        venue_name = get_venue_for_listing(a_tag)
        container_text = container.get_text(" ", strip=True)
        runtime_mins = extract_runtime(container_text)

        full_url = (
            href if href.startswith("http") else f"https://www.screenslate.com{href}"
        )

        for time_str in time_strings:
            show_time = parse_time(time_str)
            if not show_time:
                continue

            event_key = (movie_title.lower(), venue_name.lower(), time_str)
            if event_key in seen_events:
                continue
            seen_events.add(event_key)

            dt_start = tz.localize(datetime.datetime.combine(today, show_time))
            dt_end = dt_start + datetime.timedelta(minutes=runtime_mins)

            event = Event()
            event.add("summary", f"{movie_title} @ {venue_name}")
            event.add("dtstart", dt_start)
            event.add("dtend", dt_end)
            event.add("location", venue_name)
            event.add(
                "description",
                f"Movie: {movie_title}\nVenue: {venue_name}\nRuntime: {runtime_mins} mins\nLink: {full_url}",
            )

            uid_str = f"{abs(hash(movie_title + venue_name + time_str))}@{today.isoformat()}.screenslate"
            event.add("uid", uid_str)

            cal.add_component(event)
            events_added += 1

            if len(sample_events) < 3:
                sample_events.append(f"  • {movie_title} at {venue_name} ({time_str}, {runtime_mins}m)")

    ics_bytes = cal.to_ical()
    with open("screenslate.ics", "wb") as f:
        f.write(ics_bytes)

    print(f"\nSuccess! Generated 'screenslate.ics' with {events_added} screening events.")
    if sample_events:
        print("Sample events parsed:")
        for sample in sample_events:
            print(sample)


if __name__ == "__main__":
    scrape_and_build_ics()
