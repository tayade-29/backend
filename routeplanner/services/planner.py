"""
Orchestrates one trip plan:
  geocode -> routes -> checkpoints with ETAs -> weather at each ETA
  -> risk per checkpoint -> route summaries -> recommendation -> heatmap.
"""
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from . import recommend, routing, weather
from .geo import downsample, haversine_miles, sample_checkpoints
from .risk import assess, severity_score

HEATMAP_HOURS = 48
GRID_CELLS_LONG_SIDE = 22
GRID_PADDING_DEG = 0.5
MIN_CORRIDOR_MI = 30


def corridor_grid(routes):
    """Grid points over the routes' bounding box, kept only near some route.

    Returns (points, lat_step, lng_step); the steps let the client draw each
    point as a cell of the right size.
    """
    route_pts = [p for r in routes for p in downsample(r['points'], 150)]
    lats = [p[0] for p in route_pts]
    lngs = [p[1] for p in route_pts]
    south, north = min(lats) - GRID_PADDING_DEG, max(lats) + GRID_PADDING_DEG
    west, east = min(lngs) - GRID_PADDING_DEG, max(lngs) + GRID_PADDING_DEG

    cos_lat = math.cos(math.radians((south + north) / 2))
    step = max(north - south, (east - west) * cos_lat) / GRID_CELLS_LONG_SIDE  # in lat-degrees
    lng_step = step / cos_lat  # keeps cells roughly square on the ground
    corridor_mi = max(MIN_CORRIDOR_MI, step * 69)

    grid = []
    lat = south
    while lat <= north:
        lng = west
        while lng <= east:
            if min(haversine_miles((lat, lng), p) for p in route_pts) <= corridor_mi:
                grid.append((round(lat, 3), round(lng, 3)))
            lng += lng_step
        lat += step
    return grid, step, lng_step


def plan_trip(origin_text, destination_text, departure, load_lb, interval_mi):
    with ThreadPoolExecutor(max_workers=2) as pool:
        origin, destination = pool.map(routing.geocode, (origin_text, destination_text))

    routes = routing.get_routes(origin, destination, departure)

    for route in routes:
        route['checkpoints'] = sample_checkpoints(route['points'], interval_mi, route['time_knots'])

    grid, lat_step, lng_step = corridor_grid(routes)

    # One weather window that covers every checkpoint ETA and the 48h heatmap.
    longest_trip_s = max(r['duration_s'] for r in routes)
    window_end = departure + timedelta(seconds=max(HEATMAP_HOURS * 3600, longest_trip_s + 3600))
    coords = [(cp['lat'], cp['lng']) for r in routes for cp in r['checkpoints']] + grid
    forecasts = weather.get_forecasts(coords, departure, window_end)

    route_payloads, summaries = [], []
    for i, route in enumerate(routes):
        checkpoints, scored = [], []
        for cp in route['checkpoints']:
            eta = departure + timedelta(seconds=cp['offset_s'])
            wx = weather.lookup(forecasts, cp['lat'], cp['lng']).at(eta)
            risk = assess(wx['wind_mph'], wx['rain_in_hr'], wx['snow_in_hr'], load_lb)
            checkpoints.append({
                'mile': cp['mile'],
                'lat': cp['lat'],
                'lng': cp['lng'],
                'eta': eta.isoformat(),
                'segment_miles': round(cp['segment_miles'], 2),
                **wx,
                'level': int(risk.level),
                'level_label': risk.level.label,
                'reasons': list(risk.reasons),
            })
            scored.append({'level': risk.level, 'segment_miles': cp['segment_miles']})

        summary = recommend.summarize(scored, route['duration_s'], route['distance_mi'])
        summaries.append(summary)

        route_payloads.append({
            'id': i,
            'description': route['description'],
            'encoded_polyline': route['encoded_polyline'],
            'distance_mi': round(route['distance_mi'], 1),
            'duration_s': route['duration_s'],
            'arrival': (departure + timedelta(seconds=route['duration_s'])).isoformat(),
            'checkpoints': checkpoints,
            'summary': {
                'miles_by_level': summary['miles_by_level'],
                'severe_miles': round(summary['severe_miles'], 1),
                'high_miles': round(summary['high_miles'], 1),
                'no_travel_miles': round(summary['no_travel_miles'], 1),
                'avg_risk': round(summary['avg_risk'], 2),
                'max_risk': int(summary['max_risk']),
                'max_risk_label': summary['max_risk'].label,
            },
        })

    order = recommend.rank(summaries)
    for position, idx in enumerate(order):
        route_payloads[idx]['rank'] = position + 1
    best = order[0]
    runner_up = summaries[order[1]] if len(order) > 1 else None

    # Hour 0 is the departure itself, so the slider lines up with the trip.
    heat_start = departure
    heat_times = [heat_start + timedelta(hours=h) for h in range(HEATMAP_HOURS + 1)]
    heat_scores = []
    for lat, lng in grid:
        fc = weather.lookup(forecasts, lat, lng)
        heat_scores.append([
            round(severity_score(**_risk_inputs(fc.at(t)), load_lb=load_lb), 2) for t in heat_times
        ])

    return {
        'origin': origin,
        'destination': destination,
        'departure': departure.isoformat(),
        'load_lb': load_lb,
        'interval_mi': interval_mi,
        'recommended_id': best,
        'recommendation_reason': recommend.explain(summaries[best], runner_up),
        'routes': route_payloads,
        'heatmap': {
            'start': heat_start.isoformat(),
            'hours': HEATMAP_HOURS,
            'points': grid,
            'lat_step': round(lat_step, 4),
            'lng_step': round(lng_step, 4),
            # scores[point][hour], 0-5; floor() is the risk level
            'scores': heat_scores,
        },
    }


def _risk_inputs(wx):
    return {'wind_mph': wx['wind_mph'], 'rain_in_hr': wx['rain_in_hr'], 'snow_in_hr': wx['snow_in_hr']}
