import datetime
import json
import re
from bs4 import BeautifulSoup
from icalendar import Calendar, Event
import pytz
import requests

URL = "https://www.screenslate.com/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}


def parse_time(time_str):
    """Parse time strings like '7:00pm', '7pm', '7:00 PM' into datetime.time."""
    cleaned = time_str.strip().lower().replace(" ", "")
    cleaned = re.sub(r"^(\d{1,2})(am|pm)$", r"\1:00\2", cleaned)
    try:
        return datetime.datetime.strptime(cleaned, "%I:%M%p").time()
    except ValueError:
        return datetime.time(19, 0)


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

    # 1. Try parsing Next.js JSON state
    next_data_script = soup.find("script", id="__NEXT_DATA__")

    if next_data_script and next_data_script.string:
        try:
            data = json.loads(next_data_script.string)
            # Recursively search JSON for objects containing title/screening info
            json_str = json.dumps(data)

            # Extract listings from JSON state structures
            page_props = data.get("props", {}).get("pageProps", {})
            listings = page_props.get("listings", []) or page_props.get(
                "screenings", []
            )

            for item in listings:
                title = (
                    item.get("title")
                    or item.get("filmTitle")
                    or "Screening Event"
                )
                venue = (
                    item.get("venue", {}).get("name")
                    if isinstance(item.get("venue"), dict)
                    else item.get("venueName", "NYC Cinema")
                )
                time_str = item.get("time", "7:00pm")

                show_time = parse_time(str(time_str))
                dt_start = tz.localize(
                    datetime.datetime.combine(today, show_time)
                )
                dt_end = dt_start + datetime.timedelta(hours=2)

                event = Event()
                event.add("summary", f"{title} @ {venue}")
                event.add("dtstart", dt_start)
                event.add("dtend", dt_end)
                event.add("location", str(venue))
                event.add("description", f"Listing via Screen Slate: {URL}")
                cal.add_component(event)
                event_count += 1
        except Exception as err:
            print(f"JSON parsing error: {err}")

    # 2. Fallback: Parse visible DOM links if JSON extraction returned 0
    if event_count == 0:
        for link in soup.find_all("a", href=True):
            href = link["href"]
            title = link.get_text(strip=True)

            if (
                "/screenings/" in href or "/events/" in href
            ) and len(title) > 2:
                parent = link.find_parent(["li", "div", "article", "tr"])
                parent_text = parent.get_text(" ", strip=True) if parent else ""

                time_match = re.search(
                    r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b", parent_text
                )
                show_time = (
                    parse_time(time_match.group(1))
                    if time_match
                    else datetime.time(19, 0)
                )

                dt_start = tz.localize(
                    datetime.datetime.combine(today, show_time)
                )
                dt_end = dt_start + datetime.timedelta(hours=2)

                event = Event()
                event.add("summary", f"{title} @ NYC Cinema")
                event.add("dtstart", dt_start)
                event.add("dtend", dt_end)
                event.add("location", "NYC Cinema")
                full_url = (
                    href
                    if href.startswith("http")
                    else f"https://www.screenslate.com{href}"
                )
                event.add("description", f"Screen Slate Listing: {full_url}")
                cal.add_component(event)
                event_count += 1

    # Write .ics output
    with open("screenslate.ics", "wb") as f:
        f.write(cal.to_ical())

    print(f"Generated screenslate.ics with {event_count} events.")


if __name__ == "__main__":
    scrape_and_build_ics()
