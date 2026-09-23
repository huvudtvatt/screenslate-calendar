from bs4 import BeautifulSoup
from icalendar import Calendar, Event
import datetime
import re
import requests
import pytz

URL = "https://www.screenslate.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def parse_time_string(time_str):
    """Attempt to parse strings like '7:00pm', '7pm', '7:00 PM' into a time object."""
    cleaned = time_str.strip().lower().replace(" ", "")
    # Add minutes if missing (e.g., '7pm' -> '7:00pm')
    cleaned = re.sub(r"^(\d{1,2})(am|pm)$", r"\1:00\2", cleaned)
    try:
        return datetime.datetime.strptime(cleaned, "%I:%M%p").time()
    except ValueError:
        return datetime.time(19, 0)  # Default fallback: 7:00 PM


def scrape_and_build_ics():
    try:
        response = requests.get(URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"Error fetching website: {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")

    cal = Calendar()
    cal.add("prodid", "-//Screen Slate NYC Listings//screenslate.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Screen Slate NYC")

    tz = pytz.timezone("America/New_York")
    today = datetime.datetime.now(tz).date()

    # Generic container selectors for venue groups or listings
    listing_elements = soup.select(
        ".venue, .venue-group, .listing, article, .listing-item"
    )

    if not listing_elements:
        # Fallback: scan all links on page if explicit containers aren't found
        listing_elements = soup.find_all("div")

    event_count = 0
    for block in listing_elements:
        venue_el = block.find(["h2", "h3", "h4", "a"])
        venue_name = venue_el.get_text(strip=True) if venue_el else "NYC Cinema"

        # Find items within block
        items = block.find_all(["li", "p"]) or [block]
        for item in items:
            link = item.find("a")
            time_el = item.find(["time", "span"])

            if link and link.get_text(strip=True):
                title = link.get_text(strip=True)
                time_text = time_el.get_text(strip=True) if time_el else "7:00pm"

                start_time = parse_time_string(time_text)
                dt_start = tz.localize(
                    datetime.datetime.combine(today, start_time)
                )
                dt_end = dt_start + datetime.timedelta(hours=2)

                event = Event()
                event.add("summary", f"{title} @ {venue_name}")
                event.add("dtstart", dt_start)
                event.add("dtend", dt_end)
                event.add("location", venue_name)
                event.add("description", f"Listing via Screen Slate: {URL}")
                cal.add_component(event)
                event_count += 1

    with open("screenslate.ics", "wb") as f:
        f.write(cal.to_ical())

    print(f"Successfully generated screenslate.ics with {event_count} events.")


if __name__ == "__main__":
    scrape_and_build_ics()
