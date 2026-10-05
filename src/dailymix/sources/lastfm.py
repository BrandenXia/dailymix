import json
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import URLError
from ..models import Event


def text(value):
    return value.get("#text", value.get("name", "")) if isinstance(value, dict) else str(value or "")


def parse_page(payload, account=""):
    if "error" in payload:
        raise ValueError(f"Last.fm error {payload['error']}: {payload.get('message', 'Unknown error')}")
    recent = payload["recenttracks"]
    rows = recent.get("track", [])
    if isinstance(rows, dict):
        rows = [rows]
    events = []
    for row in rows:
        if row.get("@attr", {}).get("nowplaying") == "true":
            continue
        if not row.get("date", {}).get("uts"):
            raise ValueError("Completed scrobble is missing its timestamp")
        events.append(Event(text(row["artist"]), row["name"], text(row.get("album")), int(row["date"]["uts"]), account=account))
    return events, int(recent.get("@attr", {}).get("totalPages", 1))


def fetch_history(username, api_key, since=0, max_pages=10, until=None, start_page=1):
    # Fixed upper bound prevents new scrobbles from shifting pagination mid-import.
    until = int(time.time()) - 1 if until is None else until
    events = []
    page = start_page
    fetched = 0
    while True:
        params = dict(method="user.getRecentTracks", user=username, api_key=api_key, format="json", limit=200, page=page, to=until)
        if since:
            params["from"] = since
        try:
            with urlopen("https://ws.audioscrobbler.com/2.0/?" + urlencode(params), timeout=30) as response:
                payload = json.load(response)
        except URLError:
            # Avoid putting URLs containing credentials into error messages.
            raise RuntimeError("Last.fm request failed; check connectivity and retry") from None
        rows, total = parse_page(payload, username)
        events.extend(rows)
        fetched += 1
        if page >= total or fetched >= max_pages:
            return events, {"pages_fetched": fetched, "last_page": page, "next_page": page + 1 if page < total else None, "total_pages": total, "complete": page >= total, "from": since, "to": until}
        page += 1
        time.sleep(0.25)
