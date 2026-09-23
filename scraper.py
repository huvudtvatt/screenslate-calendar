from bs4 import BeautifulSoup
from icalendar import Calendar, Event
import datetime
import re
import requests
import pytz

URL = "https://www.screenslate.com/listings"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}


def parse_time(time_str):
    """Parses strings like '7:00pm', '7pm', '12:30pm' into a datetime.time object."""
    cleaned = time_str.strip().lower().replace(" ", "")
    cleaned = re.sub(r"^(\d{1,2})(am|pm)$", r"\1:00\2", cleaned)
    try:
        return datetime.datetime.strptime(cleaned, "%I:%M%p").time()
    except ValueError:
        return datetime.time(19, 0)  # Default fallback to 7:00 PM


def scrape_and_build_ics():
    try:
        res = requests.get(URL, headers=HEADERS, timeout=15)
        res.raise_for_status()
    except Exception as e:
        print(f"Fetch failed: {e}")
        return

    soup = BeautifulSoup(res.text, "html.parser")

    cal = Calendar()
    cal.add("prodid", "-//Screen Slate NYC Listings//screenslate.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Screen Slate NYC")

    tz = pytz.timezone("America/New_York")
    today = datetime.datetime.now(tz).date()

    event_count = 0

    # 1. Primary Strategy: Parse Drupal Views listing blocks
    # Screen Slate groups listings by venue or day containers
    listing_blocks = soup.select(
        ".views-row, .listing, .screening, article, li"
    )

    for block in listing_blocks:
        # Extract title
        title_el = (
            block.select_one(".title, .film-title, .views-field-title a, h4 a, h3 a")
            or block.find("a")
        )
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        # Exclude navigation links (e.g. "Previous Day", "Next Day", "On Film")
        if not title or len(title) < 2 or title in ["On Film", "Upcoming", "Previous Day", "Next Day"]:
            continue

        # Extract venue name
        venue_el = block.find_previous(["h2", "h3", "h4"], class_=lambda c: c != "title") or block.select_one(".venue, .venue-title")
        venue_name = venue_el.get_text(strip=True) if venue_el else "NYC Cinema"
        venue_name = re.sub(r"\s+", " ", venue_name).strip()

        # Extract showtimes
        block_text = block.get_text(" ", strip=True)
        times = re.findall(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b", block_text)

        if not times:
            times = ["7:00pm"]  # Default evening time if no explicit time listed

        # Link to Screen Slate event page
        href = title_el.get("href", "")
        full_url = href if href.startswith("http") else f"https://www.screenslate.com{href}"

        for time_str in set(times):
            show_time = parse_time(time_str)
            dt_start = tz.localize(datetime.datetime.combine(today, show_time))
            dt_end = dt_start + datetime.timedelta(hours=2)

            event = Event()
            event.add("summary", f"{title} @ {venue_name}")
            event.add("dtstart", dt_start)
            event.add("dtend", dt_end)
            event.add("location", venue_name)
            event.add("description", f"Screen Slate Listing: {full_url}")

            cal.add_component(event)
            event_count += 1

    # 2. Fallback Strategy: Raw DOM Link Matching (if main structure changes)
    if event_count == 0:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)

            if len(text) > 3 and not any(nav in text for nav in ["Log in", "Newsletter", "About", "Donate", "Search"]):
                parent = a.find_parent(["li", "p", "div"])
                parent_text = parent.get_text(" ", strip=True) if parent else ""

                times = re.findall(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b", parent_text)
                if times:
                    for time_str in set(times):
                        show_time = parse_time(time_str)
                        dt_start = tz.localize(datetime.datetime.combine(today, show_time))
                        dt_end = dt_start + datetime.timedelta(hours=2)

                        event = Event()
                        event.add("summary", f"{text} @ NYC Cinema")
                        event.add("dtstart", dt_start)
                        event.add("dtend", dt_end)
                        event.add("location", "NYC Cinema")
                        event.add("description", f"Screen Slate Listing: https://www.screenslate.com{href}")

                        cal.add_component(event)
                        event_count += 1

    # Write .ics file
    with open("screenslate.ics", "wb") as f:
        f.write(cal.to_ical())

    print(f"Generated screenslate.ics with {event_count} events.")


if __name__ == "__main__":
    scrape_and_build_ics()
