import datetime
import re
from bs4 import BeautifulSoup
from icalendar import Calendar, Event
import pytz
import requests

# Direct API / listings page
URL = "https://www.screenslate.com/listings"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def parse_time(time_str):
    """Converts strings like '7:00pm', '7pm', '12:30pm' into datetime.time object."""
    cleaned = time_str.strip().lower().replace(" ", "")
    cleaned = re.sub(r"^(\d{1,2})(am|pm)$", r"\1:00\2", cleaned)
    try:
        return datetime.datetime.strptime(cleaned, "%I:%M%p").time()
    except ValueError:
        return datetime.time(19, 0)


def scrape_and_build_ics():
    tz = pytz.timezone("America/New_York")
    today = datetime.datetime.now(tz).date()

    cal = Calendar()
    cal.add("prodid", "-//Screen Slate NYC Listings//screenslate.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Screen Slate NYC")

    # 1. First attempt: Query Screen Slate's JSON API directly if available
    api_url = f"https://www.screenslate.com/api/listings?date={today.strftime('%Y-%m-%d')}"
    events_added = 0

    try:
        api_res = requests.get(api_url, headers=HEADERS, timeout=10)
        if api_res.status_code == 200:
            data = api_res.json()
            listings = (
                data
                if isinstance(data, list)
                else data.get("listings", data.get("data", []))
            )

            for item in listings:
                title = (
                    item.get("title")
                    or item.get("film_title")
                    or "Screening"
                )
                venue = item.get("venue", {}).get("name") if isinstance(item.get("venue"), dict) else item.get("venue", "NYC Cinema")
                times = item.get("times", ["7:00pm"])

                for t_str in times:
                    show_time = parse_time(str(t_str))
                    dt_start = tz.localize(
                        datetime.datetime.combine(today, show_time)
                    )
                    dt_end = dt_start + datetime.timedelta(hours=2)

                    event = Event()
                    event.add("summary", f"{title} @ {venue}")
                    event.add("dtstart", dt_start)
                    event.add("dtend", dt_end)
                    event.add("location", str(venue))
                    event.add("uid", f"{hash(title + str(t_str))}@{today}")
                    cal.add_component(event)
                    events_added += 1
    except Exception:
        pass

    # 2. Fallback: Parse server-rendered listing nodes if API query was bypassed
    if events_added == 0:
        try:
            res = requests.get(URL, headers=HEADERS, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")

            # Look for venue headings and their following list elements
            current_venue = "NYC Cinema"
            for element in soup.find_all(
                ["h2", "h3", "h4", "li", "p", "article"]
            ):
                tag = element.name
                text = element.get_text(" ", strip=True)

                if tag in ["h2", "h3", "h4"] and len(text) < 60:
                    current_venue = text
                    continue

                times = re.findall(
                    r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b", text
                )
                if times and len(text) > 3:
                    title = text
                    for t in times:
                        title = title.replace(t, "")
                    title = title.strip(" *:-•\n\t")

                    if len(title) > 2:
                        for time_str in set(times):
                            show_time = parse_time(time_str)
                            dt_start = tz.localize(
                                datetime.datetime.combine(today, show_time)
                            )
                            dt_end = dt_start + datetime.timedelta(hours=2)

                            event = Event()
                            event.add(
                                "summary", f"{title} @ {current_venue}"
                            )
                            event.add("dtstart", dt_start)
                            event.add("dtend", dt_end)
                            event.add("location", current_venue)
                            event.add(
                                "uid",
                                f"{hash(title + time_str)}@{today}.screenslate",
                            )

                            cal.add_component(event)
                            events_added += 1
        except Exception as e:
            print(f"Scrape error: {e}")

    with open("screenslate.ics", "wb") as f:
        f.write(cal.to_ical())

    print(f"Generated screenslate.ics with {events_added} events.")


if __name__ == "__main__":
    scrape_and_build_ics()
