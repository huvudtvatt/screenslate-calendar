from bs4 import BeautifulSoup
from icalendar import Calendar, Event
import datetime
import re
import requests
import pytz

# Fetch direct listing feed page
URL = "https://www.screenslate.com/counties/new-york"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

def parse_time(time_str):
    """Converts strings like '7pm', '7:30pm', '7:30 PM' into datetime.time object."""
    cleaned = time_str.strip().lower().replace(" ", "")
    # Add minutes if missing: e.g. 7pm -> 7:00pm
    cleaned = re.sub(r"^(\d{1,2})(am|pm)$", r"\1:00\2", cleaned)
    try:
        return datetime.datetime.strptime(cleaned, "%I:%M%p").time()
    except ValueError:
        return datetime.time(19, 0) # Fallback 7:00 PM

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

    # Look for all article containers, listing links, or venue groupings
    containers = soup.find_all(['article', 'div', 'section'])
    
    # Process links containing screening details
    for link in soup.find_all('a', href=True):
        href = link['href']
        text = link.get_text(strip=True)
        
        # Screening links typically contain /screenings/ or /events/
        if ('/screenings/' in href or '/events/' in href or '/listings/' in href) and len(text) > 2:
            # Find closest parent element to extract venue and time context
            parent = link.find_parent(['li', 'div', 'article', 'tr'])
            context_text = parent.get_text(" ", strip=True) if parent else ""

            # Match times like 7:00pm, 7pm, 12:30 PM
            time_match = re.search(r'\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b', context_text)
            show_time = parse_time(time_match.group(1)) if time_match else datetime.time(19, 0)

            # Try extracting venue name from neighboring headers/links
            venue_el = parent.find_previous(['h2', 'h3', 'h4', 'strong']) if parent else None
            venue_name = venue_el.get_text(strip=True) if venue_el else "NYC Cinema"

            dt_start = tz.localize(datetime.datetime.combine(today, show_time))
            dt_end = dt_start + datetime.timedelta(hours=2)

            event = Event()
            event.add("summary", f"{text} @ {venue_name}")
            event.add("dtstart", dt_start)
            event.add("dtend", dt_end)
            event.add("location", venue_name)
            
            full_url = href if href.startswith("http") else f"https://www.screenslate.com{href}"
            event.add("description", f"Screen Slate Listing: {full_url}")
            
            cal.add_component(event)
            event_count += 1

    with open("screenslate.ics", "wb") as f:
        f.write(cal.to_ical())

    print(f"Generated screenslate.ics with {event_count} events.")

if __name__ == "__main__":
    scrape_and_build_ics()
