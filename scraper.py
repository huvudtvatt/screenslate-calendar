import datetime
import re
from bs4 import BeautifulSoup
from icalendar import Calendar, Event
import pytz
import requests

URL = "https://www.screenslate.com/listings"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

NAV_WORDS = {
    "screen slate",
    "screenslate",
    "listings",
    "articles",
    "about",
    "donate",
    "search",
    "home",
    "instagram",
    "twitter",
    "facebook",
    "log in",
    "sign up",
    "previous day",
    "next day",
    "screenings",
    "exhibitions",
}


def parse_time(time_str):
    """Parse time strings like '7:00 PM', '7:00pm', or '7pm' into datetime.time."""
    cleaned = time_str.strip().lower().replace(" ", "")
    cleaned = re.sub(r"^(\d{1,2})(am|pm)$", r"\1:00\2", cleaned)
    for fmt in ("%I:%M%p", "%H:%M", "%I%p"):
        try:
            return datetime.datetime.strptime(cleaned, fmt).time()
        except ValueError:
            pass
    return datetime.time(19, 0)


def extract_runtime(text):
    """Parses runtime in minutes (e.g., '112 min', '1h 45m', '95m').

    Defaults to 120 minutes (2 hours) if omitted.
    """
    h_m = re.search(
        r"\b(\d+)\s*h(?:our|ours|r)?\s*(\d+)?\s*m(?:in|ins|inute|inutes)?\b",
        text,
        re.IGNORECASE,
    )
    if h_m:
        hours = int(h_m.group(1))
        mins = int(h_m.group(2)) if h_m.group(2) else 0
        return (hours * 60) + mins

    m_only = re.search(
        r"\b(\d{2,3})\s*(?:min|mins|m|minutes)\b", text, re.IGNORECASE
    )
    if m_only:
        return int(m_only.group(1))

    return 120  # Default to 2 hours


def scrape_and_build_ics():
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        res = session.get(URL, timeout=15)
        res.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch {URL}: {e}")
        return

    soup = BeautifulSoup(res.text, "html.parser")

    tz = pytz.timezone("America/New_York")
    today = datetime.datetime.now(tz).date()

    cal = Calendar()
    cal.add("prodid", "-//Screen Slate NYC Screenings//screenslate.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Screen Slate Screenings")

    events_added = 0
    seen_events = set()
    current_venue = "Screen Slate Venue"

    main_content = soup.find("main") or soup.find("body") or soup
    elements = main_content.find_all(
        ["h1", "h2", "h3", "h4", "div", "section", "article", "li"]
    )

    for el in elements:
        text = el.get_text(strip=True)

        # 1. STOP PARSING when reaching the EXHIBITIONS header
        if el.name in ["h1", "h2", "h3", "h4"] and "EXHIBITIONS" in text.upper():
            break

        # 2. IDENTIFY VENUE HEADERS (Black ALL CAPS text blocks above movie listings)
        is_heading_tag = el.name in ["h2", "h3", "h4"]
        is_all_caps = (
            len(text) > 3
            and len(text) < 80
            and text.isupper()
            and not re.search(r"\b(\d{1,2}:\d{2}|\d{1,2}\s*(?:AM|PM))\b", text)
        )

        if is_heading_tag or is_all_caps:
            cleaned_text = text.strip()
            if cleaned_text.lower() not in NAV_WORDS:
                current_venue = cleaned_text.title() if cleaned_text.isupper() else cleaned_text
                continue

        # 3. EXTRACT MOVIE EVENTS (Black linked text + showtimes below)
        links = el.find_all("a", href=True)
        for link in links:
            movie_title = link.get_text(strip=True)
            href = link["href"]

            if not movie_title or len(movie_title) < 2 or movie_title.lower() in NAV_WORDS:
                continue

            # Venue titles shouldn't be parsed as movie titles
            if movie_title.isupper() and len(movie_title) > 5:
                continue

            parent = link.find_parent(["li", "article", "div", "p", "tr"]) or el
            context_text = parent.get_text(" ", strip=True)

            # Find showtime listed in the container text
            time_matches = re.findall(
                r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b", context_text
            )

            if not time_matches:
                continue  # Skip links that aren't screening listings with valid times

            runtime_mins = extract_runtime(context_text)
            full_url = (
                href
                if href.startswith("http")
                else f"https://www.screenslate.com{href}"
            )

            for time_str in set(time_matches):
                event_key = (movie_title.lower(), current_venue.lower(), time_str)
                if event_key in seen_events:
                    continue
                seen_events.add(event_key)

                show_time = parse_time(time_str)
                dt_start = tz.localize(
                    datetime.datetime.combine(today, show_time)
                )
                dt_end = dt_start + datetime.timedelta(minutes=runtime_mins)

                event = Event()
                event.add("summary", f"{movie_title} @ {current_venue}")
                event.add("dtstart", dt_start)
                event.add("dtend", dt_end)
                event.add("location", current_venue)
                event.add(
                    "description",
                    f"Movie: {movie_title}\nVenue: {current_venue}\nRuntime: {runtime_mins} mins\nLink: {full_url}",
                )

                uid_str = f"{abs(hash(movie_title + current_venue + time_str))}@{today.isoformat()}.screenslate"
                event.add("uid", uid_str)

                cal.add_component(event)
                events_added += 1

    ics_bytes = cal.to_ical()
    with open("screenslate.ics", "wb") as f:
        f.write(ics_bytes)

    print(
        f"Generated screenslate.ics with {events_added} screening events."
    )


if __name__ == "__main__":
    scrape_and_build_ics()
