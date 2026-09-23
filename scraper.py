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

IGNORED_TITLE_WORDS = {
    "screen slate", "screenslate", "listings", "articles", "about",
    "donate", "search", "home", "instagram", "twitter", "facebook",
    "log in", "sign up", "previous day", "next day", "screenings",
    "exhibitions", "calendar", "archive", "read more", "view details"
}

TIME_REGEX = r"\b(\d{1,2}(?::\d{2})?\s*(?:[aApP]\.?[mM]\.?|[aApP]))\b"


def parse_time(time_str):
    """Parse time strings like '7:00 PM', '7:00pm', '7pm', '12:30p' into a datetime.time object."""
    cleaned = time_str.strip().lower().replace(".", "").replace(" ", "")
    cleaned = re.sub(r"^(\d{1,2})(am|pm|a|p)$", r"\1:00\2", cleaned)
    cleaned = re.sub(r"([ap])$", r"\1m", cleaned)
    
    for fmt in ("%I:%M%p", "%H:%M", "%I%p"):
        try:
            return datetime.datetime.strptime(cleaned, fmt).time()
        except ValueError:
            pass
    return None


def extract_runtime(text):
    """Extract runtime in minutes from listing text (e.g. '112 min', '1h 45m').
    Defaults to 120 minutes (2 hours) if omitted.
    """
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

    m_only = re.search(
        r"\b(\d{2,3})\s*(?:min|mins|m|minutes)\b", text, re.IGNORECASE
    )
    if m_only:
        return int(m_only.group(1))

    return 120


def get_listing_container(a_tag):
    """Find the tightest parent container around a movie title link that includes showtimes."""
    curr = a_tag.parent
    while curr and curr.name not in ["html", "body", "main"]:
        text = curr.get_text(" ", strip=True)
        times = re.findall(TIME_REGEX, text)
        if times:
            other_links = [
                link for link in curr.find_all("a")
                if link != a_tag and len(link.get_text(strip=True)) > 2
            ]
            if len(other_links) <= 3:
                return curr, times
        curr = curr.parent
    return None, []


def get_venue_for_listing(a_tag):
    """Search backwards from a listing link to find the closest venue header."""
    for prev in a_tag.find_all_previous(
        ["h1", "h2", "h3", "h4", "h5", "div", "p", "section", "header", "b", "strong"]
    ):
        text = prev.get_text(" ", strip=True)
        if not text or len(text) < 3 or len(text) > 100:
            continue

        # Venue headers do not contain movie showtimes
        if re.search(TIME_REGEX, text):
            continue

        low = text.lower()
        if any(
            term in low
            for term in [
                "screenings", "exhibitions", "screen slate", "donate",
                "subscribe", "newsletter", "search", "menu"
            ]
        ):
            continue

        is_heading = prev.name in ["h1", "h2", "h3", "h4", "h5"]
        classes = [c.lower() for c in prev.get("class", [])]
        has_venue_class = any(
            "venue" in c or "location" in c or "cinema" in c or "theater" in c
            for c in classes
        )

        if is_heading or has_venue_class:
            return text.title() if text.isupper() else text

        if prev.name in ["div", "p", "strong", "b", "span"]:
            if not prev.find("a", href=re.compile(r"/listings/|/films/|/movies/")):
                if len(text.split()) <= 8:
                    return text.title() if text.isupper() else text

    return "Screen Slate Venue"


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

    # Remove site headers, nav, footers, scripts to prevent false context matches
    for tag in soup(["script", "style", "nav", "header", "footer", "form"]):
        tag.decompose()

    main_content = (
        soup.find("main")
        or soup.find(id=re.compile(r"content", re.I))
        or soup.find("body")
        or soup
    )

    # 1. Stop processing at EXHIBITIONS section by removing the section and all trailing siblings
    for tag in main_content.find_all(["h1", "h2", "h3", "h4", "h5", "div", "section"]):
        tag_text = tag.get_text(strip=True).upper()
        if tag_text == "EXHIBITIONS" or tag_text.startswith("EXHIBITIONS "):
            curr = tag
            while curr:
                nxt = curr.next_sibling
                curr.decompose()
                curr = nxt
            break

    tz = pytz.timezone("America/New_York")
    today = datetime.datetime.now(tz).date()

    cal = Calendar()
    cal.add("prodid", "-//Screen Slate NYC Screenings//screenslate.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Screen Slate Screenings")

    events_added = 0
    seen_events = set()

    # 2. Extract movie screenings based on link tags and surrounding showtimes
    for a_tag in main_content.find_all("a", href=True):
        movie_title = a_tag.get_text(strip=True)
        href = a_tag["href"]

        if not movie_title or len(movie_title) < 2:
            continue
        if movie_title.lower() in IGNORED_TITLE_WORDS:
            continue

        container, time_strings = get_listing_container(a_tag)
        if not time_strings or not container:
            continue

        venue_name = get_venue_for_listing(a_tag)
        container_text = container.get_text(" ", strip=True)
        runtime_mins = extract_runtime(container_text)

        full_url = (
            href
            if href.startswith("http")
            else f"https://www.screenslate.com{href}"
        )

        for time_str in set(time_strings):
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

    ics_bytes = cal.to_ical()
    with open("screenslate.ics", "wb") as f:
        f.write(ics_bytes)

    print(f"Generated screenslate.ics with {events_added} screening events.")


if __name__ == "__main__":
    scrape_and_build_ics()
