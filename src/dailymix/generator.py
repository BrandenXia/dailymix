from collections import Counter
from datetime import date, datetime, time, timedelta
import hashlib
import math
from .models import normalize, timestamp
from .scoring import ALGORITHM_VERSION, settings, quantile, skip_risk, utility


def stable_hash(*parts):
    return hashlib.sha256('\0'.join(map(str, parts)).encode()).hexdigest()


def generate(tracks, features, day, config, history, timezone):
    cfg = config['mix']
    values = settings(config)
    cutoff = datetime.combine(day, time.min, timezone)
    pools, size = cfg['pools'], cfg['size']
    if size < 1 or any(v < 0 for v in pools.values()) or sum(pools.values()) != size:
        raise ValueError('Pool quotas must be nonnegative and sum to mix size')
    if set(pools) != {'familiar', 'rediscovery', 'recent', 'exploration'}:
        raise ValueError('Expected familiar, rediscovery, recent, exploration pools')
    if cfg['artist_limit'] < 1 or cfg['album_limit'] < 1:
        raise ValueError('Artist and album limits must be positive')
    primary_codes, associations = config['tags']['primary'], config['tags']['associations']
    configured_targets = cfg['primary_targets']
    if any(not math.isfinite(x) or x < 0 for x in configured_targets.values()):
        raise ValueError('Primary targets must be finite and nonnegative')
    tracks = sorted(tracks, key=lambda t: t.id)
    by_id = {t.id: t for t in tracks}

    def primary(t):
        tags = [code for code in primary_codes if code in t.tags]
        return tags[0] if len(tags) == 1 else 'unknown'

    def association(t):
        tags = sorted(t.tags.intersection(associations))
        return tags[0] if len(tags) == 1 else ('mixed' if tags else 'unspecified')

    def keys(t):
        return normalize(t.artist) or t.id, (normalize(t.artist), normalize(t.album)) if t.album else (t.id, '')

    recent_ids, exposures, artist_exposures = set(), Counter(), Counter()
    for previous_day, mix in sorted(history.items()):
        gap = (day - date.fromisoformat(previous_day)).days
        if gap <= 0:
            continue
        weight = 2 ** (-gap / values['exposure_half_life_days'])
        for item in mix['tracks']:
            if gap <= cfg['mix_repeat_days']:
                recent_ids.add(item['id'])
            exposures[item['id']] += weight
            if item['id'] in by_id:
                artist_exposures[keys(by_id[item['id']])[0]] += weight

    # Being offered a song is not evidence of listening or of dislike.
    previous_mix = history.get((day - timedelta(days=1)).isoformat(), {'tracks': []})
    heard_ids, heard_artists, heard_tags, heard_primary = set(), Counter(), Counter(), Counter()
    for item in previous_mix['tracks']:
        if item['id'] not in by_id:
            continue
        t, f = by_id[item['id']], features[item['id']]
        heard = f.plays_yesterday > 0 or (f.last_played and f.last_played.astimezone(timezone).date() == day - timedelta(days=1))
        if heard:
            heard_ids.add(t.id)
            # Each heard song contributes once, preventing one looped song dominating.
            heard_artists[keys(t)[0]] += 1
            heard_tags[association(t)] += 1
            heard_primary[primary(t)] += 1

    # Content profile blends catalog coverage with long/recent matched listening
    # and confirmed listening to songs offered yesterday. Absent songs do not
    # inherit local tags; their exact-artist affinity remains available separately.
    def content_targets(category_for, feedback, configured_prior=None):
        catalog_profile, recent_profile, long_profile = Counter(), Counter(), Counter()
        for t in tracks:
            if not t.enabled:
                continue
            category = category_for(t)
            catalog_profile[category] += 1
            recent_profile[category] += features[t.id].recent_plays
            long_profile[category] += features[t.id].long_term_plays
        base = configured_prior if configured_prior and sum(configured_prior.values()) else catalog_profile
        categories = sorted(set(catalog_profile) | set(base))
        total = sum(base.values()) or 1
        prior = {c: base.get(c, 0) / total for c in categories}

        def smoothed(profile):
            support = sum(profile.values())
            return {c: (profile[c] + 10 * prior[c]) / (support + 10) for c in categories}

        r, l, y = smoothed(recent_profile), smoothed(long_profile), smoothed(feedback)
        rw, lw, yw = (values[k] for k in ('recent_profile_weight', 'long_profile_weight', 'previous_mix_profile_weight'))
        shares = {c: (1-rw-lw-yw)*prior[c] + rw*r[c] + lw*l[c] + yw*y[c] for c in categories}
        return {c: shares[c]*size for c in categories}

    association_targets = content_targets(association, heard_tags)
    targets = content_targets(primary, heard_primary, configured_targets)
    evidence_values = [max(t.plays, features[t.id].scrobbles) for t in tracks if t.enabled]
    familiar_threshold = max(5, quantile(evidence_values, values['familiar_quantile']))
    exploration_threshold = max(2, quantile(evidence_values, values['exploration_quantile']))
    candidates = {pool: [] for pool in pools}
    eligible, metadata, excluded = [], {}, Counter()
    for t in tracks:
        if not t.enabled:
            excluded['disabled'] += 1
            continue
        if t.id in recent_ids:
            excluded['mix_cooldown'] += 1
            continue
        f = features[t.id]
        days_since = (cutoff - f.last_played).total_seconds() / 86400 if f.last_played else math.inf
        if days_since < cfg['cooldown_days']:
            excluded['listening_cooldown'] += 1
            continue
        added = timestamp(t.added)
        if added and added.tzinfo is None:
            raise ValueError('Catalog dates must include a timezone')
        age = (cutoff - added).total_seconds() / 86400 if added else math.inf
        if age < 0:
            excluded['future_addition'] += 1
            continue
        evidence = max(t.plays, f.scrobbles)
        risk = skip_risk(t, evidence)
        if risk >= 0.65:
            excluded['high_skip_risk'] += 1
            continue
        membership = []
        if (evidence >= familiar_threshold or t.rating >= 80) and risk <= 0.35:
            membership.append('familiar')
        if evidence >= 2 and days_since >= cfg['rediscovery_days'] and risk <= 0.35:
            membership.append('rediscovery')
        if age <= cfg['recent_days']:
            membership.append('recent')
        if evidence <= exploration_threshold and age >= cfg['exploration_age_days'] and risk <= 0.25:
            membership.append('exploration')
        for pool in membership:
            candidates[pool].append(t)
        eligible.append(t)
        metadata[t.id] = (age, days_since)
    selected, ids = [], set()
    artist_counts, album_counts, primary_counts, assoc_counts, category_counts, pool_counts = (Counter() for _ in range(6))

    def allowed(t):
        artist, album = keys(t)
        return t.id not in ids and artist_counts[artist] < cfg['artist_limit'] and album_counts[album] < cfg['album_limit']

    def rank(t, actual, step):
        artist, _ = keys(t)
        feedback = min(1, heard_artists[artist] / 2)  # bounded direct artist feedback
        exposure = exposures[t.id] + artist_exposures[artist] * 0.25
        base, components = utility(t, features[t.id], actual, *metadata[t.id], feedback, exposure, values)
        # Exponential-race keys give all eligible tracks a chance, weighted by
        # preference, without allowing the same popular songs to win every day.
        u = (int(stable_hash(ALGORITHM_VERSION, day, actual, t.id)[:13], 16) + 1) / (16**13 + 1)
        weight = math.exp(max(-10, min(10, base * values['preference_strength'])))
        race = -math.log(u) / weight
        progress = step / size
        primary_deficit = targets.get(primary(t), 0) * progress - primary_counts[primary(t)]
        assoc_deficit = association_targets.get(association(t), 0) * progress - category_counts[association(t)]
        final = math.log(max(race, 1e-15)) - values['primary_balance_weight'] * primary_deficit - values['association_balance_weight'] * assoc_deficit
        adjacent = bool(selected and keys(selected[-1][0])[0] == artist)
        explanation = {'preference_utility': round(base, 6), 'components': {k: round(v, 6) for k, v in components.items()},
                       'weighted_rotation_key': round(race, 6), 'primary_deficit': round(primary_deficit, 6),
                       'association_deficit': round(assoc_deficit, 6)}
        return (adjacent, final, t.id), explanation

    slots = [pool for i in range(max(pools.values())) for pool, count in pools.items() if i < count]
    fallbacks = []
    for requested in slots:
        chosen = None
        # Prefer fallback pools still below their quotas instead of always taking
        # familiar songs. Membership is reevaluated after every constrained pick.
        other_pools = sorted((p for p in pools if p != requested),
                             key=lambda p: (-(pools[p] - pool_counts[p]) / max(1, pools[p]), p))
        for actual in [requested] + other_pools + ['eligible']:
            choices = [t for t in (eligible if actual == 'eligible' else candidates[actual]) if allowed(t)]
            if choices:
                scored = [(rank(t, actual, len(selected)+1), t) for t in choices]
                (key, explanation), chosen = min(scored, key=lambda item: item[0][0])
                break
        if chosen is None:
            break
        t = chosen
        artist, album = keys(t)
        artist_counts[artist] += 1
        album_counts[album] += 1
        primary_counts[primary(t)] += 1
        assoc_counts.update(sorted(t.tags.intersection(associations)))
        category_counts[association(t)] += 1
        pool_counts[actual] += 1
        ids.add(t.id)
        selected.append((t, actual, requested, explanation))
        if actual != requested:
            fallbacks.append({'requested': requested, 'used': actual, 'id': t.id})
    return {'algorithm_version': ALGORITHM_VERSION, 'date': day.isoformat(), 'requested_size': size, 'actual_size': len(selected),
            'primary_counts': dict(primary_counts), 'association_counts': dict(assoc_counts),
            'association_targets': {c: round(v, 3) for c, v in association_targets.items()},
            'primary_targets': {c: round(v, 3) for c, v in targets.items()},
            'pool_counts': dict(pool_counts), 'candidate_counts': {p: len(c) for p, c in candidates.items()},
            'adaptive_thresholds': {'familiar': familiar_threshold, 'exploration': exploration_threshold},
            'previous_mix_feedback': {'offered': len(previous_mix['tracks']), 'heard': len(heard_ids),
                                      'heard_ids': sorted(heard_ids), 'playlist_origin_known': False},
            'excluded_counts': dict(excluded), 'fallbacks': fallbacks, 'shortfall': size-len(selected),
            'tracks': [{'id': t.id, 'title': t.title, 'artist': t.artist, 'album': t.album,
                        'tags': sorted(t.tags), 'pool': actual, 'requested_pool': requested,
                        'reason': {'music_plays': t.plays, 'matched_scrobbles': features[t.id].scrobbles,
                                   'artist_scrobbles': features[t.id].artist_plays,
                                   'recent_artist_plays': round(features[t.id].recent_artist_plays, 3),
                                   'last_played': features[t.id].last_played.isoformat() if features[t.id].last_played else None,
                                   **explanation}}
                       for t, actual, requested, explanation in selected]}
