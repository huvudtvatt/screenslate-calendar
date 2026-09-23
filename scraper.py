import datetime
import json
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
    """Parse time strings like '7:00pm', '7pm', '12:30pm' into datetime.time object."""
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

    # 1. Try parsing JSON-LD / Next.js embedded state
    scripts = soup.find_all("script", type="application/ld+json") or soup.find_all(
        "script", id="__NEXT_DATA__"
    )

    for script in scripts:
        if not script.string:
            continue
        try:
            data = json.loads(script.string)

            # Process Schema.org Event structures if available
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "Event":
                    title = item.get("name", "Screening")
                    location = item.get("location", {}).get("name", "NYC Cinema")
                    start_str = item.get("startDate")

                    dt_start = (
                        datetime.datetime.fromisoformat(start_str)
                        if start_str
                        else tz.localize(
                            datetime.datetime.combine(
                                today, datetime.time(19, 0)
                            )
                        )
                    )
                    dt_end = dt_start + datetime.timedelta(hours=2)

                    event = Event()
                    event.add("summary", f"{title} @ {location}")
                    event.add("dtstart", dt_start)
                    event.add("dtend", dt_end)
                    event.add("location", str(location))
                    event.add("description", f"Screen Slate Listing: {URL}")

                    cal.add_component(event)
                    event_count += 1
        except Exception:
            pass

    # 2. Fallback: Parse visible venue blocks directly
    if event_count == 0:
        # Screen Slate structures listings under venue containers or paragraphs
        raw_text = soup.get_text("\n")
        current_venue = "NYC Cinema"

        for line in raw_text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Identify time patterns like 7:00pm, 12:15pm, 6:50pm
            times = re.findall(
                r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b", line
            )

            if times:
                title = line
                for t in times:
                    title = title.replace(t, "")
                title = title.strip(" *:-•\n\t")

                if len(title) > 2:
                    for t_str in times:
                        show_time = parse_time(t_str)
                        dt_start = tz.localize(
                            datetime.datetime.combine(today, show_time)
                        )
                        dt_end = dt_start + datetime.timedelta(hours=2)

                        event = Event()
                        event.add("summary", f"{title} @ {current_venue}")
                        event.add("dtstart", dt_start)
                        event.add("dtend", dt_end)
                        event.add("location", current_venue)
                        event.add("description", f"Screen Slate Listing: {URL}")

                        cal.add_component(event)
                        event_count += 1
            elif (
                len(line) < 60
                and not line.startswith("http")
                and not any(char.isdigit() for char in line)
            ):
                # Update venue context heading (e.g. "Film Forum", "Metrograph")
                current_venue = line

    with open("screenslate.ics", "wb") as f:
        f.write(cal.to_ical())

    print(f"Generated screenslate.ics with {event_count} events.")


if __name__ == "__main__":
    scrape_and_build_ics()
