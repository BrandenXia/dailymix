from dataclasses import asdict
from datetime import datetime, time
import hashlib
import json
from pathlib import Path
from .features import build_features
from .generator import generate
from .models import normalize
from .sources.apple_music import load_catalog


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def select_mix(store, day, timezone, zone_name, config, catalog_loader, username=None, overrides_path=None, save=False):
    with store.transaction():
        saved = store.mix(day.isoformat())
        if saved:
            if saved.get('timezone') != zone_name:
                raise ValueError('This date was frozen in a different timezone')
            return {**saved, 'cached': True}
        tracks = load_catalog(catalog_loader())
        events = store.events(username or config.get('lastfm', {}).get('username') or None)
        overrides = {}
        if overrides_path:
            for row in json.loads(Path(overrides_path).read_text()):
                overrides[tuple(normalize(row[k]) for k in ('artist', 'title', 'album'))] = row['track_id']
        cutoff = datetime.combine(day, time.min, timezone)
        features, matches = build_features(tracks, events, cutoff, overrides)
        history = store.history(day.isoformat())
        mix = generate(tracks, features, day, config, history, timezone)
        mix.update({'schema_version': 1, 'timezone': zone_name, 'matching': matches,
                    'input_fingerprint': fingerprint({'tracks': sorted([asdict(t) for t in tracks], key=lambda t: t['id']),
                                                     'events': [asdict(e) for e in events], 'config': config,
                                                     'overrides': sorted((list(k), v) for k, v in overrides.items()),
                                                     'history': history, 'date': day.isoformat(), 'timezone': zone_name}),
                    'algorithm_version': '0.1.0'})
        if save:
            store.save_mix(day.isoformat(), mix)
        return mix
