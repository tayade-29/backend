"""
Google Geocoding + Routes API.

Google returns 1-3 routes depending on the road network. The spec asks for
three options, so when Google returns fewer we request detours through a via
point pushed off to each side of the main route, and drop any result that
mostly overlaps a route we already have.
"""
import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from django.conf import settings
from django.core.cache import cache

from .errors import PlannerError, UpstreamError
from .geo import METERS_PER_MILE, cumulative_miles, decode_polyline, downsample, haversine_miles

GEOCODE_URL = 'https://maps.googleapis.com/maps/api/geocode/json'
ROUTES_URL = 'https://routes.googleapis.com/directions/v2:computeRoutes'
FIELD_MASK = ','.join([
    'routes.distanceMeters',
    'routes.duration',
    'routes.description',
    'routes.polyline.encodedPolyline',
    'routes.legs.steps.distanceMeters',
    'routes.legs.steps.staticDuration',
])
TIMEOUT_S = 15

TARGET_ROUTES = 3
DETOUR_OFFSETS = (0.15, 0.30)  # fraction of trip length to push the via point sideways
DUPLICATE_OVERLAP = 0.85       # share of a route within OVERLAP_MI of another -> same route
OVERLAP_MI = 2.0


def _api_key():
    if not settings.GOOGLE_MAPS_API_KEY:
        raise PlannerError('Server is missing GOOGLE_MAPS_API_KEY', status=500)
    return settings.GOOGLE_MAPS_API_KEY


def geocode(address):
    # Hash so spaces/unicode in addresses can't break cache backends like memcached.
    cache_key = 'geocode:' + hashlib.sha1(address.strip().lower().encode()).hexdigest()
    if (hit := cache.get(cache_key)) is not None:
        return hit

    try:
        resp = requests.get(GEOCODE_URL, params={'address': address, 'key': _api_key()},
                            timeout=TIMEOUT_S)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise UpstreamError('Geocoding service is unavailable') from exc

    if data.get('status') == 'ZERO_RESULTS':
        raise PlannerError(f'Could not find a location for "{address}"')
    if data.get('status') != 'OK':
        raise UpstreamError(f'Geocoding failed: {data.get("status")}')

    top = data['results'][0]
    place = {
        'address': top['formatted_address'],
        'lat': top['geometry']['location']['lat'],
        'lng': top['geometry']['location']['lng'],
    }
    cache.set(cache_key, place, 60 * 60 * 24)
    return place


def _waypoint(lat, lng, via=False):
    point = {'location': {'latLng': {'latitude': lat, 'longitude': lng}}}
    if via:
        point['via'] = True  # pass through without creating a stop/leg
    return point


def _parse_seconds(value):
    return int(value.rstrip('s')) if value else 0


def _parse_route(raw):
    points = decode_polyline(raw['polyline']['encodedPolyline'])
    poly_miles = cumulative_miles(points)[-1]
    duration_s = _parse_seconds(raw.get('duration'))

    # Build (mile, seconds) knots from per-step distances and durations so the
    # ETA along the route reflects each road's speed. Steps are rescaled so they
    # agree with the polyline length and the traffic-aware total duration.
    steps = [s for leg in raw.get('legs', []) for s in leg.get('steps', [])]
    step_m = sum(s.get('distanceMeters', 0) for s in steps)
    step_s = sum(_parse_seconds(s.get('staticDuration')) for s in steps)
    knots = [(0.0, 0.0)]
    if step_m and step_s:
        mile_scale = poly_miles / (step_m / METERS_PER_MILE)
        time_scale = duration_s / step_s
        m = s_ = 0.0
        for step in steps:
            m += step.get('distanceMeters', 0) / METERS_PER_MILE * mile_scale
            s_ += _parse_seconds(step.get('staticDuration')) * time_scale
            knots.append((m, s_))
    else:
        knots.append((poly_miles, float(duration_s)))

    return {
        'description': raw.get('description') or 'Route',
        'encoded_polyline': raw['polyline']['encodedPolyline'],
        'points': points,
        'distance_mi': raw.get('distanceMeters', 0) / METERS_PER_MILE,
        'duration_s': duration_s,
        'time_knots': knots,
    }


def _compute_routes(origin, destination, departure, via=None):
    body = {
        'origin': _waypoint(origin['lat'], origin['lng']),
        'destination': _waypoint(destination['lat'], destination['lng']),
        'travelMode': 'DRIVE',
        'routingPreference': 'TRAFFIC_AWARE',
        'languageCode': 'en-US',  # otherwise road names come back in the local script
        # Google rejects past departure times for traffic-aware routing.
        'departureTime': max(departure, datetime.now(timezone.utc) + timedelta(minutes=1))
                         .strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    if via:
        body['intermediates'] = [_waypoint(*via, via=True)]
    else:
        body['computeAlternativeRoutes'] = True

    headers = {'X-Goog-Api-Key': _api_key(), 'X-Goog-FieldMask': FIELD_MASK}
    try:
        resp = requests.post(ROUTES_URL, json=body, headers=headers, timeout=TIMEOUT_S)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise UpstreamError('Routing service is unavailable') from exc

    if resp.status_code != 200:
        message = data.get('error', {}).get('message', resp.status_code)
        raise UpstreamError(f'Routing failed: {message}')
    return [_parse_route(r) for r in data.get('routes', [])]


def overlap_fraction(a_points, b_points):
    """Share of route A that lies within OVERLAP_MI of route B."""
    probes = downsample(a_points, 40)
    targets = downsample(b_points, 600)
    near = sum(1 for p in probes if min(haversine_miles(p, t) for t in targets) <= OVERLAP_MI)
    return near / len(probes)


def _is_duplicate(candidate, existing):
    return any(overlap_fraction(candidate['points'], r['points']) >= DUPLICATE_OVERLAP
               for r in existing)


def _detour_via_points(route):
    """Points pushed perpendicular to the trip's overall direction, at its midpoint."""
    points = route['points']
    (lat1, lng1), (lat2, lng2) = points[0], points[-1]
    mid_lat, mid_lng = points[len(points) // 2]

    # Work in a local flat projection: 1 deg lat ~ 69 mi, 1 deg lng ~ 69*cos(lat) mi.
    cos_lat = math.cos(math.radians(mid_lat))
    dx, dy = (lng2 - lng1) * cos_lat, lat2 - lat1
    length = math.hypot(dx, dy) or 1
    nx, ny = -dy / length, dx / length  # unit normal (rotated 90 degrees)

    trip_mi = haversine_miles(points[0], points[-1])
    vias = []
    for fraction in DETOUR_OFFSETS:
        offset_deg = trip_mi * fraction / 69.0
        for side in (1, -1):
            vias.append((mid_lat + side * ny * offset_deg,
                         mid_lng + side * nx * offset_deg / cos_lat))
    return vias


def get_routes(origin, destination, departure):
    routes = _compute_routes(origin, destination, departure)
    if not routes:
        raise PlannerError('No drivable route found between these locations')

    if len(routes) < TARGET_ROUTES:
        vias = _detour_via_points(routes[0])
        with ThreadPoolExecutor(max_workers=len(vias)) as pool:
            results = pool.map(lambda v: _safe_compute(origin, destination, departure, v), vias)
            for candidates in results:
                for candidate in candidates:
                    if len(routes) >= TARGET_ROUTES:
                        break
                    if not _is_duplicate(candidate, routes):
                        candidate['description'] += ' (detour)'
                        routes.append(candidate)

    return routes[:TARGET_ROUTES]


def _safe_compute(origin, destination, departure, via):
    # A via point can land somewhere undrivable (lake, mountain); just skip it.
    try:
        return _compute_routes(origin, destination, departure, via=via)
    except PlannerError:
        return []
