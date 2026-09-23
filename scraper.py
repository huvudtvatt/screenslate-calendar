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
        " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}


def parse_time(time_str):
    """Converts strings like '7:00pm', '7pm', '12:30pm' into datetime.time object."""
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

    # Look for list items or elements containing showtimes
    # Screen Slate groups listings under headers or container blocks
    blocks = soup.find_all(["li", "div", "p", "article"])

    for block in blocks:
        text = block.get_text(" ", strip=True)

        # Match time patterns like 7:00pm, 12:45pm, 6:30pm
        times = re.findall(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b", text
        )

        if times and len(text) < 500:
            # Extract venue from previous header or parent
            venue_el = block.find_previous(["h2", "h3", "h4", "strong"])
            venue_name = (
                venue_el.get_text(strip=True) if venue_el else "NYC Cinema"
            )

            # Clean up the movie title text (removing raw time strings)
            title = text
            for t in times:
                title = title.replace(t, "")
            title = title.strip(" *:-•\n\t")

            # Extract links if available
            link_el = block.find("a", href=True)
            href = link_el["href"] if link_el else "/listings"
            full_url = (
                href
                if href.startswith("http")
                else f"https://www.screenslate.com{href}"
            )

            if len(title) > 2:
                for time_str in times:
                    show_time = parse_time(time_str)
                    dt_start = tz.localize(
                        datetime.datetime.combine(today, show_time)
                    )
                    dt_end = dt_start + datetime.timedelta(hours=2)

                    event = Event()
                    event.add("summary", f"{title} @ {venue_name}")
                    event.add("dtstart", dt_start)
                    event.add("dtend", dt_end)
                    event.add("location", venue_name)
                    event.add(
                        "description", f"Screen Slate Listing: {full_url}"
                    )

                    cal.add_component(event)
                    event_count += 1

    with open("screenslate.ics", "wb") as f:
        f.write(cal.to_ical())

    print(f"Generated screenslate.ics with {event_count} events.")


if __name__ == "__main__":
    scrape_and_build_ics()
