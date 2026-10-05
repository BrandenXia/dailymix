import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, time
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib
from zoneinfo import ZoneInfo
from .features import build_features
from .generator import generate
from .models import Event
from .sources.apple_music import read_music, load_catalog
from .sources.lastfm import parse_page, fetch_history
from .state import Store


def emit(payload, output=None):
    value = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output:
        path = Path(output)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(value)
        temporary.replace(path)
    else:
        print(value, end="")


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Apple Music Daily Mix previews")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--state", default="state/history.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("snapshot", help="Read Music metadata; never modify the library")
    export.add_argument("--output", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--catalog", required=True)
    imported = commands.add_parser("import-lastfm", help="Import saved API responses or normalized events")
    imported.add_argument("file")
    imported.add_argument("--username", required=True)
    sync = commands.add_parser("sync-lastfm", help="Fetch history with LASTFM_API_KEY")
    sync.add_argument("--username")
    sync.add_argument("--from-timestamp", type=int, default=0)
    sync.add_argument("--max-pages", type=int, default=10)
    sync.add_argument("--to-timestamp", type=int, help="Reuse the reported upper bound when resuming")
    sync.add_argument("--start-page", type=int, default=1)
    for name in ("preview", "generate"):
        p = commands.add_parser(name, help="Preview" if name == "preview" else "Freeze a local daily mix; no playlist writes")
        p.add_argument("--catalog", required=True)
        p.add_argument("--date", default=None)
        p.add_argument("--timezone", default="America/Indiana/Indianapolis")
        p.add_argument("--username", help="Use only this Last.fm account")
        p.add_argument("--overrides", help="JSON array of artist/title/album/track_id mappings")
        p.add_argument("--output")
    args = parser.parse_args(argv)
    store = None
    try:
        if args.command == "snapshot":
            emit(read_music(), args.output)
            return
        if args.command == "inspect":
            tracks = load_catalog(json.loads(Path(args.catalog).read_text()))
            emit({"tracks": len(tracks), "tags": dict(Counter(tag for t in tracks for tag in t.tags)),
                  "groupings": dict(Counter(t.grouping for t in tracks))})
            return
        config = tomllib.loads(Path(args.config).read_text())
        Path(args.state).parent.mkdir(parents=True, exist_ok=True)
        store = Store(args.state)
        if args.command == "import-lastfm":
            payload = json.loads(Path(args.file).read_text())
            if "recenttracks" in payload or "error" in payload:
                events, pages = parse_page(payload, args.username)
                extra = {"response_total_pages": pages}
            else:
                events = [Event(**{**row, "account": args.username, "source": "lastfm"}) for row in payload["events"]]
                extra = {}
            emit({"imported": store.import_events(events), "received": len(events), **extra})
            return
        if args.command == "sync-lastfm":
            username = args.username or config.get("lastfm", {}).get("username")
            key = os.environ.get("LASTFM_API_KEY")
            if not username or not key:
                raise ValueError("Set a Last.fm username and LASTFM_API_KEY")
            if args.max_pages < 1 or args.from_timestamp < 0 or args.start_page < 1:
                raise ValueError("Page counts must be positive and from-timestamp nonnegative")
            if args.start_page > 1 and args.to_timestamp is None:
                raise ValueError("Resuming requires --to-timestamp from the previous import")
            if args.to_timestamp is not None and args.to_timestamp < args.from_timestamp:
                raise ValueError("to-timestamp must be at least from-timestamp")
            events, progress = fetch_history(username, key, args.from_timestamp, args.max_pages, args.to_timestamp, args.start_page)
            emit({"imported": store.import_events(events), **progress})
            return
        timezone = ZoneInfo(args.timezone)
        day = date.fromisoformat(args.date) if args.date else datetime.now(timezone).date()
        with store.transaction():
            saved = store.mix(day.isoformat())
            if saved:
                if saved.get("timezone") != args.timezone:
                    raise ValueError("This date was frozen in a different timezone")
                emit({**saved, "cached": True}, args.output)
                return
            tracks = load_catalog(json.loads(Path(args.catalog).read_text()))
            events = store.events(args.username or config.get("lastfm", {}).get("username") or None)
            overrides = {}
            if args.overrides:
                from .models import normalize
                for row in json.loads(Path(args.overrides).read_text()):
                    overrides[tuple(normalize(row[k]) for k in ("artist", "title", "album"))] = row["track_id"]
            cutoff = datetime.combine(day, time.min, timezone)
            features, matches = build_features(tracks, events, cutoff, overrides)
            history = store.history(day.isoformat())
            mix = generate(tracks, features, day, config, history, timezone)
            mix.update({"schema_version": 1, "timezone": args.timezone, "matching": matches,
                        "input_fingerprint": fingerprint({"tracks": sorted([asdict(t) for t in tracks], key=lambda t: t["id"]),
                                                          "events": [asdict(e) for e in events], "config": config,
                                                          "overrides": sorted((list(k), v) for k, v in overrides.items()), "history": history,
                                                          "date": day.isoformat(), "timezone": args.timezone}),
                        "algorithm_version": "0.1.0", "published": False})
            if args.command == "generate":
                store.save_mix(day.isoformat(), mix)
        emit(mix, args.output)
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        parser.exit(1, f"dailymix: {error}\n")
    finally:
        if store:
            store.close()
