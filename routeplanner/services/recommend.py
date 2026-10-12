"""
Summarize each route's risk and pick the recommended one.

Ranking (from the spec), compared one criterion at a time:
  0. Fewest No Travel miles  (assumption: No Travel is worse than Severe)
  1. Fewest Severe miles
  2. Fewest High miles
  3. Lowest average risk     (mile-weighted mean of Low=0 .. No Travel=4)
  4. Shortest travel time
"""
from .risk import Risk

# Differences smaller than this are treated as ties so that floating-point
# noise (24.999 vs 25.0 miles) doesn't decide the ranking.
MILES_EPSILON = 0.5
AVG_EPSILON = 0.01


def summarize(checkpoints, duration_s, distance_mi):
    """checkpoints: [{'level': Risk, 'segment_miles': float}, ...]"""
    miles_by_level = {level: 0.0 for level in Risk}
    for cp in checkpoints:
        miles_by_level[cp['level']] += cp['segment_miles']

    covered = sum(miles_by_level.values())
    avg = sum(level * mi for level, mi in miles_by_level.items()) / covered if covered else 0.0

    return {
        'distance_mi': distance_mi,
        'duration_s': duration_s,
        'miles_by_level': {level.label: round(mi, 1) for level, mi in miles_by_level.items()},
        'no_travel_miles': miles_by_level[Risk.NO_TRAVEL],
        'severe_miles': miles_by_level[Risk.SEVERE],
        'high_miles': miles_by_level[Risk.HIGH],
        'avg_risk': avg,
        'max_risk': max((cp['level'] for cp in checkpoints), default=Risk.LOW),
    }


def _bucket(value, epsilon):
    return round(value / epsilon)


def rank_key(summary):
    # Python compares tuples element by element, which is exactly
    # "prefer A, then break ties with B, then C...".
    return (
        _bucket(summary['no_travel_miles'], MILES_EPSILON),
        _bucket(summary['severe_miles'], MILES_EPSILON),
        _bucket(summary['high_miles'], MILES_EPSILON),
        _bucket(summary['avg_risk'], AVG_EPSILON),
        summary['duration_s'],
    )


def rank(summaries):
    """Return route indices ordered best first."""
    return sorted(range(len(summaries)), key=lambda i: rank_key(summaries[i]))


def explain(best, runner_up):
    """One sentence on why `best` beat `runner_up`, for the UI."""
    if runner_up is None:
        return 'This was the only route available.'
    criteria = [
        ('no_travel_miles', 'fewer miles in No Travel conditions', MILES_EPSILON),
        ('severe_miles', 'fewer miles of Severe weather', MILES_EPSILON),
        ('high_miles', 'fewer miles of High-risk weather', MILES_EPSILON),
        ('avg_risk', 'milder weather overall', AVG_EPSILON),
    ]
    for field, phrase, eps in criteria:
        if _bucket(best[field], eps) != _bucket(runner_up[field], eps):
            return f'It has {phrase} than the other routes.'
    return 'Weather is equally safe on every route, so the fastest one is recommended.'
