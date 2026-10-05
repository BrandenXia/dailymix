from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from .models import normalize, timestamp
from .matching import Resolver


@dataclass(frozen=True)
class Features:
    scrobbles: int = 0
    last_played: datetime | None = None
    artist_plays: int = 0


def build_features(tracks, events, cutoff, overrides=None):
    resolver = Resolver(tracks, overrides)
    counts, artists, latest, statuses = Counter(), Counter(), {}, Counter()
    for event in events:
        at = datetime.fromtimestamp(event.timestamp, timezone.utc)
        if at >= cutoff:
            continue
        artists[normalize(event.artist)] += 1
        match = resolver.resolve(event)
        statuses[match.status] += 1
        if match.track_id:
            counts[match.track_id] += 1
            latest[match.track_id] = max(latest.get(match.track_id, at), at)
    features = {}
    for t in tracks:
        local = timestamp(t.last_played)
        if local and local.tzinfo is None:
            raise ValueError("Catalog dates must include a timezone")
        # Local future dates must not contaminate historical generation.
        local = local if local and local < cutoff else None
        dates = [d for d in (local, latest.get(t.id)) if d]
        features[t.id] = Features(counts[t.id], max(dates) if dates else None, artists[normalize(t.artist)])
    return features, dict(statuses)
