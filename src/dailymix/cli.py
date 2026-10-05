import argparse
from collections import Counter
from datetime import date, datetime
import json
from pathlib import Path
import tomllib
from zoneinfo import ZoneInfo
from .models import Event
from .sources.apple_music import read_music, load_catalog
from .sources.lastfm import parse_page, fetch_history
from .state import Store
from .service import select_mix
from .publication import publish_mix
from .schedule import write_schedule
from .credentials import lastfm_api_key


def emit(payload, output=None):
    value = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(value)
        temporary.replace(path)
    else:
        print(value, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deterministic Apple Music mixes and optional playlist publishing")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--state", default="state/history.sqlite3")
    parser.add_argument("--env-file", help="Credentials file; defaults to .env beside config.toml")
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
    publish = commands.add_parser("publish", help="Publish a frozen mix to a managed Music playlist")
    publish.add_argument("--date")
    publish.add_argument("--timezone", default="America/Indiana/Indianapolis")
    publish.add_argument("--playlist", default="Daily Mix")
    publish.add_argument("--dry-run", action="store_true")
    publish.add_argument("--output")
    run = commands.add_parser("run", help="Read Music and freeze today's mix; optionally publish")
    run.add_argument("--date")
    run.add_argument("--timezone", default="America/Indiana/Indianapolis")
    run.add_argument("--username")
    run.add_argument("--overrides")
    run.add_argument("--playlist", default="Daily Mix")
    run.add_argument("--publish", action="store_true")
    run.add_argument("--sync-lastfm", action="store_true", help="Refresh up to ten recent history pages if an API key is available")
    run.add_argument("--output")
    schedule = commands.add_parser("schedule", help="Export a launchd job; does not install or start it")
    schedule.add_argument("--output", required=True)
    schedule.add_argument("--hour", type=int, default=3)
    schedule.add_argument("--minute", type=int, default=0)
    schedule.add_argument("--timezone", default="America/Indiana/Indianapolis")
    schedule.add_argument("--publish", action="store_true")
    schedule.add_argument("--sync-lastfm", action="store_true")
    schedule.add_argument("--playlist", default="Daily Mix")
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
        if args.command == "schedule":
            ZoneInfo(args.timezone)
            emit(write_schedule(args.output, args.config, args.state, args.hour, args.minute,
                                args.publish, args.playlist, args.timezone, args.sync_lastfm))
            return
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
            key = lastfm_api_key(args.env_file or Path(args.config).resolve().with_name(".env"))
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
        if args.command == "publish":
            mix = store.mix(day.isoformat())
            if not mix:
                raise ValueError("No frozen mix for this date; run generate or run first")
            if mix.get("timezone") != args.timezone:
                raise ValueError("This date was frozen in a different timezone")
            emit(publish_mix(store, args.state, mix, args.playlist, args.dry_run), args.output)
            return
        warnings = []
        if args.command == "run":
            username = args.username or config.get("lastfm", {}).get("username")
            if args.sync_lastfm and not store.mix(day.isoformat()):
                key = lastfm_api_key(args.env_file or Path(args.config).resolve().with_name(".env"))
                if key and username:
                    try:
                        since = max(0, int(datetime.now(timezone).timestamp()) - 45 * 86400)
                        events, progress = fetch_history(username, key, since=since)
                        store.import_events(events)
                        if not progress["complete"]:
                            warnings.append("Recent Last.fm refresh reached its page limit; use sync-lastfm to finish importing")
                    except (RuntimeError, ValueError, OSError) as error:
                        warnings.append(str(error) + "; using cached listening history")
                else:
                    warnings.append("Last.fm key or username missing; using cached listening history")
            catalog_loader = read_music
        else:
            catalog_loader = lambda: json.loads(Path(args.catalog).read_text())
        mix = select_mix(store, day, timezone, args.timezone, config, catalog_loader,
                         args.username, args.overrides, save=args.command in ("generate", "run"))
        if args.command == "run" and args.publish:
            publication = publish_mix(store, args.state, mix, args.playlist)
            mix = {**mix, "publication": publication}
        mix = {**mix, "publications": store.publications(day.isoformat())}
        if warnings:
            mix = {**mix, "warnings": warnings}
        emit(mix, args.output)
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        parser.exit(1, f"dailymix: {error}\n")
    finally:
        if store:
            store.close()
