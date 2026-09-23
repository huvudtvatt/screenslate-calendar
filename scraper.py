import datetime
import re
import sys
from bs4 import BeautifulSoup
from icalendar import Calendar, Event
import pytz
import requests

TARGET_URLS = [
    "https://www.screenslate.com/",
    "https://www.screenslate.com/listings",
    "https://www.screenslate.com/articles",
]

KNOWN_VENUES = [
    "Film Forum",
    "Metrograph",
    "Roxy Cinema",
    "IFC Center",
    "Nitehawk",
    "Anthology Film Archives",
    "MoMA",
    "Museum of Modern Art",
    "BAM",
    "Paris Theater",
    "Quad Cinema",
    "Museum of the Moving Image",
    "Film at Lincoln Center",
    "Spectacle Theater",
    "Spectacle",
    "Symphony Space",
    "DCTV",
    "Elinor Bunin",
    "Walter Reade",
]

IGNORED_WORDS = {
    "about",
    "donate",
    "subscribe",
    "search",
    "contact",
    "privacy",
    "terms",
    "user",
    "archive",
    "home",
    "next",
    "previous",
    "log in",
    "sign up",
    "podcast",
    "editorial",
    "articles",
    "listings",
    "screen slate",
    "instagram",
    "twitter",
    "facebook",
}


def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": (
            '"Chromium";v="128", "Not=A?Brand";v="24", "Google Chrome";v="128"'
        ),
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    })
    return s


def parse_time(time_str):
    if not time_str:
        return datetime.time(19, 0)
    cleaned = time_str.strip().lower().replace(" ", "")
    cleaned = re.sub(r"^(\d{1,2})(am|pm)$", r"\1:00\2", cleaned)

    for fmt in ("%I:%M%p", "%H:%M", "%I:%M"):
        try:
            return datetime.datetime.strptime(cleaned, fmt).time()
        except ValueError:
            pass
    return datetime.time(19, 0)


def extract_events_from_html(html_content, base_url, tz, today):
    soup = BeautifulSoup(html_content, "html.parser")

    # Decompose structural elements that contain non-listing links
    for tag in soup(["script", "style", "nav", "footer", "svg", "header"]):
        tag.decompose()

    events = []
    seen_keys = set()

    links = soup.find_all("a", href=True)
    print(f"DEBUG: Found {len(links)} candidate links on {base_url}")

    for a in links:
        title = a.get_text(strip=True)
        href = a["href"]

        if not title or len(title) < 2 or title.lower() in IGNORED_WORDS:
            continue
        if any(
            skip in href.lower()
            for skip in [
                "javascript:",
                "mailto:",
                "/about",
                "/donate",
                "/subscribe",
                "/search",
                "/user",
                "/privacy",
                "twitter.com",
                "instagram.com",
                "facebook.com",
            ]
        ):
            continue

        parent = a.find_parent(["li", "article", "div", "p", "tr", "section"])
        context_text = parent.get_text(" ", strip=True) if parent else title

        time_matches = re.findall(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b|\b([012]?\d:[05]\d)\b",
            context_text,
        )

        times_found = []
        for match in time_matches:
            t = match[0] or match[1]
            if t and t not in times_found:
                times_found.append(t)

        if not times_found and len(title) > 3 and "/" in href:
            times_found = ["7:00pm"]

        venue = "Screen Slate NYC"
        for kv in KNOWN_VENUES:
            if kv.lower() in context_text.lower():
                venue = kv
                break

        if venue == "Screen Slate NYC" and parent:
            heading = parent.find_previous(["h1", "h2", "h3", "h4", "strong"])
            if heading:
                h_text = heading.get_text(strip=True)
                if 2 < len(h_text) < 50 and h_text.lower() not in IGNORED_WORDS:
                    venue = h_text

        full_url = (
            href
            if href.startswith("http")
            else f"https://www.screenslate.com{href}"
        )

        for time_str in times_found:
            key = (title.lower(), venue.lower(), time_str)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            show_time = parse_time(time_str)
            dt_start = tz.localize(datetime.datetime.combine(today, show_time))
            dt_end = dt_start + datetime.timedelta(hours=2)

            event = Event()
            event.add("summary", f"{title} @ {venue}")
            event.add("dtstart", dt_start)
            event.add("dtend", dt_end)
            event.add("location", venue)
            event.add("description", f"Screen Slate Listing: {full_url}")

            uid_str = f"{abs(hash(title + venue + time_str))}@{today.isoformat()}.screenslate"
            event.add("uid", uid_str)

            events.append(event)

    return events


def scrape_and_build_ics():
    session = get_session()
    tz = pytz.timezone("America/New_York")
    today = datetime.datetime.now(tz).date()

    all_events = []

    for url in TARGET_URLS:
        print(f"\n--- Fetching Target: {url} ---")
        try:
            resp = session.get(url, timeout=15)
            print(f"HTTP Status Code: {resp.status_code}")
            print(f"Response Body Length: {len(resp.text)} characters")

            if resp.status_code != 200:
                print(f"Non-200 status code received for {url}. Skipping...")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            title_tag = soup.find("title")
            page_title = (
                title_tag.get_text(strip=True) if title_tag else "No Title"
            )
            print(f"Page Title: '{page_title}'")

            if "Just a moment" in page_title or "Cloudflare" in page_title:
                print("Cloudflare anti-bot challenge detected. Skipping...")
                continue

            events = extract_events_from_html(resp.text, url, tz, today)
            print(f"Extracted {len(events)} valid events from {url}")

            if len(events) > 0:
                all_events.extend(events)
                break

        except Exception as e:
            print(f"Request failed for {url}: {e}")

    print(
        f"\nTotal unique events parsed across targets: {len(all_events)}"
    )

    cal = Calendar()
    cal.add("prodid", "-//Screen Slate NYC Listings//screenslate.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Screen Slate NYC")

    for ev in all_events:
        cal.add_component(ev)

    ics_data = cal.to_ical()

    with open("screenslate.ics", "wb") as f:
        f.write(ics_data)

    print(f"Successfully generated screenslate.ics ({len(ics_data)} bytes).")


if __name__ == "__main__":
    scrape_and_build_ics()
