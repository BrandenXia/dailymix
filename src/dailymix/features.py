from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from .models import normalize, timestamp
from .matching import Resolver


@dataclass(frozen=True)
class Features:
    scrobbles: int = 0
    last_played: datetime | None = None
    artist_plays: int = 0
    recent_plays: float = 0
    long_term_plays: float = 0
    recent_artist_plays: float = 0
    long_term_artist_plays: float = 0
    plays_yesterday: int = 0


def build_features(tracks, events, cutoff, overrides=None, config=None):
    settings = (config or {}).get('recommendation', {})
    recent_half = settings.get('recent_half_life_days', 14)
    long_half = settings.get('long_half_life_days', 180)
    if any(not math.isfinite(x) or x <= 0 for x in (recent_half, long_half)):
        raise ValueError('Listening half-lives must be positive and finite')
    resolver = Resolver(tracks, overrides)
    counts, artists, latest, statuses = Counter(), Counter(), {}, Counter()
    recent, long_term, recent_artists, long_artists, yesterday = (Counter() for _ in range(5))
    # Stable accumulation makes features invariant to import enumeration order.
    for event in sorted(events, key=lambda e: e.key):
        at = datetime.fromtimestamp(event.timestamp, timezone.utc)
        if at >= cutoff:
            continue
        age = (cutoff - at).total_seconds() / 86400
        recent_weight = 2 ** (-age / recent_half)
        long_weight = 2 ** (-age / long_half)
        artist = normalize(event.artist)
        artists[artist] += 1
        recent_artists[artist] += recent_weight
        long_artists[artist] += long_weight
        match = resolver.resolve(event)
        statuses[match.status] += 1
        if match.track_id:
            identifier = match.track_id
            counts[identifier] += 1
            recent[identifier] += recent_weight
            long_term[identifier] += long_weight
            if at.astimezone(cutoff.tzinfo).date().toordinal() == cutoff.date().toordinal() - 1:
                yesterday[identifier] += 1
            latest[identifier] = max(latest.get(identifier, at), at)
    features = {}
    for t in tracks:
        local = timestamp(t.last_played)
        if local and local.tzinfo is None:
            raise ValueError('Catalog dates must include a timezone')
        local = local if local and local < cutoff else None
        dates = [d for d in (local, latest.get(t.id)) if d]
        artist = normalize(t.artist)
        features[t.id] = Features(counts[t.id], max(dates) if dates else None, artists[artist],
                                  recent[t.id], long_term[t.id], recent_artists[artist],
                                  long_artists[artist], yesterday[t.id])
    return features, dict(sorted(statuses.items()))
