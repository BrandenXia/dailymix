"""Bounded preference signals and deterministic weighted rotation."""
import math

ALGORITHM_VERSION = '0.2.0'
DEFAULTS = {
    'recent_half_life_days': 14, 'long_half_life_days': 180,
    'familiar_quantile': 0.5, 'exploration_quantile': 0.25,
    'exposure_half_life_days': 14,
    'recent_profile_weight': 0.45, 'long_profile_weight': 0.25,
    'previous_mix_profile_weight': 0.2,
    'preference_strength': 2.5,
    'primary_balance_weight': 0.45, 'association_balance_weight': 0.3,
    'artist_recent_weight': 0.25, 'artist_long_weight': 0.1,
    'previous_mix_weight': 0.2, 'exposure_weight': 0.2,
}


def settings(config):
    values = {**DEFAULTS, **config.get('recommendation', {})}
    for key, value in values.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f'Invalid recommendation setting: {key}')
    for key in ('recent_half_life_days', 'long_half_life_days', 'exposure_half_life_days'):
        if values[key] <= 0:
            raise ValueError(f'{key} must be positive')
    for key in ('familiar_quantile', 'exploration_quantile'):
        if values[key] > 1:
            raise ValueError(f'{key} must be between zero and one')
    if sum(values[k] for k in ('recent_profile_weight', 'long_profile_weight', 'previous_mix_profile_weight')) > 1:
        raise ValueError('Profile weights must sum to at most one')
    return values


def quantile(values, fraction):
    if not values:
        return 0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def log_signal(value, scale=10):
    return math.log1p(max(0, value)) / (math.log1p(max(0, value)) + math.log1p(scale))


def skip_risk(track, evidence):
    # A few skips need more evidence before they dominate a recommendation.
    return max(0, track.skips) / (max(0, evidence) + max(0, track.skips) + 10)


def utility(track, feature, pool, age, days_since, feedback, exposure, values):
    evidence = max(track.plays, feature.scrobbles)
    familiarity = log_signal(evidence, 20)
    overdue = 1 if math.isinf(days_since) else 1 - math.exp(-max(0, days_since) / 60)
    recent_addition = 0 if math.isinf(age) else math.exp(-max(0, age) / 45)
    unexplored = 1 - familiarity
    intent = {'familiar': familiarity, 'rediscovery': familiarity * overdue,
              'recent': recent_addition, 'exploration': unexplored,
              'eligible': 0.5}[pool]
    components = {
        'intent': 0.55 * intent,
        'rating': 0.15 * (track.rating / 100 if track.rating else 0.5),
        'recent_artist_affinity': values['artist_recent_weight'] * log_signal(feature.recent_artist_plays),
        'long_term_artist_affinity': values['artist_long_weight'] * log_signal(feature.long_term_artist_plays, 50),
        'previous_mix_affinity': values['previous_mix_weight'] * feedback,
        'skip_penalty': -0.7 * skip_risk(track, evidence),
        'mix_exposure_penalty': -values['exposure_weight'] * log_signal(exposure, 2),
    }
    return sum(components.values()), components
