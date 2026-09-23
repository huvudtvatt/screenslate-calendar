from bs4 import BeautifulSoup
from icalendar import Calendar, Event
import datetime
import requests
import pytz

URL = "https://www.screenslate.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def scrape_and_build_ics():
    response = requests.get(URL, headers=HEADERS)
    soup = BeautifulSoup(response.text, "html.parser")

    cal = Calendar()
    cal.add("prodid", "-//Screen Slate NYC Listings//screenslate.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Screen Slate NYC")

    tz = pytz.timezone("America/New_York")
    today = datetime.datetime.now(tz).date()

    # Find cinema listing blocks on Screen Slate
    venues = soup.find_all("div", class_="venue-group") or soup.find_all(
        "div", class_="listing"
    )

    for venue in venues:
        venue_name_el = venue.find("h3") or venue.find("a", class_="venue-title")
        venue_name = (
            venue_name_el.get_text(strip=True) if venue_name_el else "NYC Cinema"
        )

        for show in venue.find_all("li"):
            title_el = show.find("a")
            time_el = show.find("span", class_="time") or show.find(
                "time"
            )  # adapt based on exact markup

            if title_el:
                title = title_el.get_text(strip=True)
                time_str = time_el.get_text(strip=True) if time_el else "7:00pm"

                # Parse simple time strings like "7:00pm" or "12:15pm"
                try:
                    start_time = datetime.datetime.strptime(time_str, "%I:%M%p").time()
                except ValueError:
                    start_time = datetime.time(19, 0)  # default 7:00 PM if unparseable

                dt_start = tz.localize(datetime.datetime.combine(today, start_time))
                dt_end = dt_start + datetime.timedelta(hours=2)

                event = Event()
                event.add("summary", f"{title} @ {venue_name}")
                event.add("dtstart", dt_start)
                event.add("dtend", dt_end)
                event.add("location", venue_name)
                event.add("description", f"Listing via Screen Slate: {URL}")
                cal.add_component(event)

    with open("screenslate.ics", "wb") as f:
        f.write(cal.to_ical())


if __name__ == "__main__":
    scrape_and_build_ics()