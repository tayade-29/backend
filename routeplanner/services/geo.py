"""
Geometry helpers: decode route polylines, measure distance, and place
checkpoints every N miles along a route together with the time the truck
reaches each one.
"""
import math
from bisect import bisect_left

EARTH_RADIUS_MI = 3958.8
METERS_PER_MILE = 1609.344
MIN_FINAL_SEGMENT_MI = 1.0


def decode_polyline(encoded):
    """Decode a Google encoded polyline into [(lat, lng), ...].

    Format: each coordinate is stored as a delta from the previous one,
    multiplied by 1e5, zig-zag encoded and split into 5-bit chunks offset by 63.
    https://developers.google.com/maps/documentation/utilities/polylinealgorithm
    """
    points = []
    index = lat = lng = 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):  # latitude, then longitude
            shift = result = 0
            while True:
                chunk = ord(encoded[index]) - 63
                index += 1
                result |= (chunk & 0x1F) << shift
                shift += 5
                if chunk < 0x20:  # no continuation bit -> last chunk
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        points.append((lat / 1e5, lng / 1e5))
    return points


def haversine_miles(a, b):
    """Great-circle distance in miles between two (lat, lng) points."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(h))


def cumulative_miles(points):
    """Distance from the start to each point: [0, d1, d1+d2, ...]."""
    totals = [0.0]
    for prev, cur in zip(points, points[1:]):
        totals.append(totals[-1] + haversine_miles(prev, cur))
    return totals


def downsample(points, n):
    """At most n points, evenly spaced by index, always keeping both ends."""
    if len(points) <= n:
        return points
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)] for i in range(n)]


def _interpolate(xs, ys, x):
    """Linear interpolation of y at x, where xs is sorted ascending."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_left(xs, x)
    x0, x1 = xs[i - 1], xs[i]
    t = 0 if x1 == x0 else (x - x0) / (x1 - x0)
    return ys[i - 1] + t * (ys[i] - ys[i - 1])


def point_at_mile(points, cum, mile):
    lat = _interpolate(cum, [p[0] for p in points], mile)
    lng = _interpolate(cum, [p[1] for p in points], mile)
    return lat, lng


def sample_checkpoints(points, interval_mi, time_knots):
    """Place a checkpoint at mile 0, every `interval_mi` miles, and at the end.

    time_knots: [(mile, seconds_from_departure), ...] ascending. Built from the
    routing API's per-step durations, so the ETA reflects slower city streets
    vs. faster highways instead of assuming one constant speed.

    Each checkpoint "owns" the stretch of road halfway to its neighbours
    (`segment_miles`), so segment miles add up to the route length. That is what
    lets us report "N Severe miles" per route.
    """
    if len(points) < 2:
        raise ValueError('A route needs at least two points')

    cum = cumulative_miles(points)
    total = cum[-1]

    miles = [i * interval_mi for i in range(int(total // interval_mi) + 1)]
    remainder = total - miles[-1]
    if len(miles) > 1 and remainder < MIN_FINAL_SEGMENT_MI:
        miles[-1] = total  # snap onto the destination instead of a near-duplicate
    elif remainder > 0:
        miles.append(total)

    knot_miles = [k[0] for k in time_knots]
    knot_secs = [k[1] for k in time_knots]

    checkpoints = []
    for i, mile in enumerate(miles):
        start = mile if i == 0 else (miles[i - 1] + mile) / 2
        end = mile if i == len(miles) - 1 else (mile + miles[i + 1]) / 2
        lat, lng = point_at_mile(points, cum, mile)
        checkpoints.append({
            'mile': round(mile, 2),
            'lat': round(lat, 5),
            'lng': round(lng, 5),
            'offset_s': _interpolate(knot_miles, knot_secs, mile),
            'segment_miles': end - start,
        })
    return checkpoints
