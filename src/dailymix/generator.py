from collections import Counter
from datetime import date, datetime, time
import hashlib
import math
from .models import normalize, timestamp


def stable_hash(*parts):
    return hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()


def generate(tracks, features, day, config, history, timezone):
    cfg = config["mix"]
    cutoff = datetime.combine(day, time.min, timezone)
    pools = cfg["pools"]
    size = cfg["size"]
    if size < 1 or any(v < 0 for v in pools.values()) or sum(pools.values()) != size:
        raise ValueError("Pool quotas must be nonnegative and sum to mix size")
    if set(pools) != {"familiar", "rediscovery", "recent", "exploration"}:
        raise ValueError("Expected familiar, rediscovery, recent, exploration pools")
    if cfg["artist_limit"] < 1 or cfg["album_limit"] < 1:
        raise ValueError("Artist and album limits must be positive")
    primary_codes = config["tags"]["primary"]
    associations = config["tags"]["associations"]
    targets = cfg["primary_targets"]
    recent_ids = set()
    for previous_day, mix in history.items():
        gap = (day - date.fromisoformat(previous_day)).days
        if 0 < gap <= cfg["mix_repeat_days"]:
            recent_ids.update(item["id"] for item in mix["tracks"])
    candidates = {pool: [] for pool in pools}
    eligible = []
    for t in tracks:
        if not t.enabled or t.id in recent_ids:
            continue
        f = features[t.id]
        days_since = (cutoff - f.last_played).total_seconds() / 86400 if f.last_played else math.inf
        if days_since < cfg["cooldown_days"]:
            continue
        added = timestamp(t.added)
        if added and added.tzinfo is None:
            raise ValueError("Catalog dates must include a timezone")
        age = (cutoff - added).total_seconds() / 86400 if added else math.inf
        if age < 0:
            continue
        # Events and local aggregates overlap; do not sum their counts.
        evidence = max(t.plays, f.scrobbles)
        low_skips = t.skips <= max(1, evidence / 2)
        membership = []
        if (evidence >= 5 or t.rating >= 80) and low_skips:
            membership.append("familiar")
        if evidence >= 2 and days_since >= cfg["rediscovery_days"] and low_skips:
            membership.append("rediscovery")
        if age <= cfg["recent_days"]:
            membership.append("recent")
        if evidence <= 2 and age >= cfg["exploration_age_days"] and t.skips <= 1:
            membership.append("exploration")
        for pool in membership:
            candidates[pool].append(t)
        eligible.append(t)
    selected, ids = [], set()
    artist_counts, album_counts, primary_counts, assoc_counts = Counter(), Counter(), Counter(), Counter()

    def primary(t):
        tags = [code for code in primary_codes if code in t.tags]
        return tags[0] if len(tags) == 1 else "unknown"

    def keys(t):
        return normalize(t.artist) or t.id, (normalize(t.artist), normalize(t.album)) if t.album else (t.id, "")

    def allowed(t):
        artist, album = keys(t)
        return t.id not in ids and artist_counts[artist] < cfg["artist_limit"] and album_counts[album] < cfg["album_limit"]

    def rank(t, pool):
        f = features[t.id]
        progress = (len(selected) + 1) / size
        deficit = targets.get(primary(t), 0) * progress - primary_counts[primary(t)]
        category_penalty = sum(assoc_counts[x] for x in t.tags if x in associations) / max(1, len(selected))
        artist, _ = keys(t)
        adjacent = bool(selected and keys(selected[-1][0])[0] == artist)
        # Stable daily variation has more influence than lifetime popularity.
        rotation = int(stable_hash(day, pool, t.id)[:13], 16) / 16**13
        score = rotation * 2 + deficit * 0.5 - category_penalty * 0.25
        score += min(math.log1p(f.artist_plays), 5) * 0.05
        score += t.rating / 100 * 0.15 - min(t.skips, 10) * 0.03
        return (adjacent, -score, t.id)

    # Round-robin pool slots prevent long stretches of one intent.
    slots = [pool for i in range(max(pools.values())) for pool, count in pools.items() if i < count]
    fallbacks = []
    for requested in slots:
        chosen = None
        for actual in [requested] + [p for p in pools if p != requested] + ["eligible"]:
            choices = [t for t in (eligible if actual == "eligible" else candidates[actual]) if allowed(t)]
            if choices:
                chosen = min(choices, key=lambda t: rank(t, requested))
                break
        if chosen is None:
            break
        t = chosen
        artist, album = keys(t)
        artist_counts[artist] += 1
        album_counts[album] += 1
        primary_counts[primary(t)] += 1
        assoc_counts.update(x for x in t.tags if x in associations)
        ids.add(t.id)
        selected.append((t, actual, requested))
        if actual != requested:
            fallbacks.append({"requested": requested, "used": actual, "id": t.id})
    return {"date": day.isoformat(), "requested_size": size, "actual_size": len(selected),
            "primary_counts": dict(primary_counts), "association_counts": dict(assoc_counts),
            "fallbacks": fallbacks, "shortfall": size - len(selected),
            "tracks": [{"id": t.id, "title": t.title, "artist": t.artist, "album": t.album,
                        "tags": sorted(t.tags), "pool": actual, "requested_pool": requested,
                        "reason": {"music_plays": t.plays, "matched_scrobbles": features[t.id].scrobbles,
                                   "artist_scrobbles": features[t.id].artist_plays,
                                   "last_played": features[t.id].last_played.isoformat() if features[t.id].last_played else None}}
                       for t, actual, requested in selected]}
