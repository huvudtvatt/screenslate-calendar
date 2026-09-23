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
    cleaned = time_str.strip().lower().replace(" ", "")
    cleaned = re.sub(r"^(\d{1,2})(am|pm)$", r"\1:00\2", cleaned)
    try:
        return datetime.datetime.strptime(cleaned, "%I:%M%p").time()
    except ValueError:
        return datetime.time(19, 0)


def scrape_and_build_ics():
    cal = Calendar()
    cal.add("prodid", "-//Screen Slate NYC Listings//screenslate.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Screen Slate NYC")

    tz = pytz.timezone("America/New_York")
    today = datetime.datetime.now(tz).date()

    try:
        res = requests.get(URL, headers=HEADERS, timeout=15)
        res.raise_for_status()
    except Exception as e:
        print(f"Fetch failed: {e}")
        return

    soup = BeautifulSoup(res.text, "html.parser")
    events_list = []

    # Target links that represent actual screenings/listings
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)

        # Screen Slate screening links usually contain /screenings/ or /events/ or /articles/
        if any(path in href for path in ["/screenings/", "/events/", "/listings/"]) and len(text) > 2:
            if text in ["Screen Slate", "Listings", "Articles", "About", "Donate", "Search", "Next Day", "Previous Day"]:
                continue

            parent = a.find_parent(["li", "p", "div", "article"])
            parent_text = parent.get_text(" ", strip=True) if parent else ""

            # Find showtimes like 7:00pm, 6:30 PM
            times = re.findall(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b", parent_text)
            if not times:
                times = ["7:00pm"]

            # Try to infer venue name from nearest heading
            venue_el = parent.find_previous(["h2", "h3", "h4"]) if parent else None
            venue_name = venue_el.get_text(strip=True) if venue_el else "NYC Cinema"

            full_url = href if href.startswith("http") else f"https://www.screenslate.com{href}"

            for t_str in set(times):
                show_time = parse_time(t_str)
                dt_start = tz.localize(datetime.datetime.combine(today, show_time))
                dt_end = dt_start + datetime.timedelta(hours=2)

                event = Event()
                event.add("summary", f"{text} @ {venue_name}")
                event.add("dtstart", dt_start)
                event.add("dtend", dt_end)
                event.add("location", venue_name)
                event.add("description", f"Screen Slate Listing: {full_url}")

                # Unique UID to ensure Google Calendar recognizes individual events
                uid = f"{hash(full_url + t_str)}@{today.isoformat()}.screenslate"
                event.add("uid", uid)

                events_list.append(event)

    # Add all collected events to the main calendar object
    for ev in events_list:
        cal.add_component(ev)

    # Save to disk
    ics_data = cal.to_ical()
    with open("screenslate.ics", "wb") as f:
        f.write(ics_data)

    print(f"Generated screenslate.ics with {len(events_list)} events.")


if __name__ == "__main__":
    scrape_and_build_ics()
