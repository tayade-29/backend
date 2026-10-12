"""
Hourly forecasts from Open-Meteo (free, no API key).

Many coordinates go in one request (comma-separated lists), batches run in
parallel, and each location's series is cached so re-planning the same trip
doesn't refetch.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from django.core.cache import cache

from .errors import UpstreamError

FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
BATCH_SIZE = 100
TIMEOUT_S = 20
COORD_DECIMALS = 2  # ~1 km; finer than the forecast model's own grid
CACHE_TTL_S = 60 * 30


def floor_hour(dt):
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


class Forecast:
    """Hourly series for one location, starting at `start` (UTC, on the hour)."""

    def __init__(self, start, wind, gust, rain, snow):
        self.start = start
        self.wind, self.gust, self.rain, self.snow = wind, gust, rain, snow

    def index_at(self, when):
        offset_h = round((when - self.start).total_seconds() / 3600)
        return min(max(offset_h, 0), len(self.wind) - 1)

    def at(self, when):
        i = self.index_at(when)
        return {
            'wind_mph': self.wind[i],
            'gust_mph': self.gust[i],
            'rain_in_hr': self.rain[i],
            'snow_in_hr': self.snow[i],
        }


def _key(lat, lng):
    return round(lat, COORD_DECIMALS), round(lng, COORD_DECIMALS)


def _fetch_batch(coords, start, end):
    params = {
        'latitude': ','.join(f'{lat}' for lat, _ in coords),
        'longitude': ','.join(f'{lng}' for _, lng in coords),
        'hourly': 'wind_speed_10m,wind_gusts_10m,rain,showers,snowfall',
        'wind_speed_unit': 'mph',
        'precipitation_unit': 'inch',  # rain and snowfall both in inches per hour
        'timezone': 'GMT',
        'start_hour': start.strftime('%Y-%m-%dT%H:%M'),
        'end_hour': end.strftime('%Y-%m-%dT%H:%M'),
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=TIMEOUT_S)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise UpstreamError('Weather service is unavailable') from exc
    if resp.status_code != 200:
        raise UpstreamError(f'Weather lookup failed: {data.get("reason", resp.status_code)}')

    results = data if isinstance(data, list) else [data]  # single location -> plain object
    forecasts = []
    for item in results:
        h = item['hourly']
        zero = lambda xs: [x or 0.0 for x in xs]  # Open-Meteo uses null for missing
        forecasts.append(Forecast(
            start=start,
            wind=zero(h['wind_speed_10m']),
            gust=zero(h['wind_gusts_10m']),
            rain=[r + s for r, s in zip(zero(h['rain']), zero(h['showers']))],
            snow=zero(h['snowfall']),
        ))
    return forecasts


def get_forecasts(coords, start, end):
    """Return {(lat, lng) rounded: Forecast} covering [start, end] hourly."""
    start, end = floor_hour(start), floor_hour(end)
    window = f'{start:%Y%m%d%H}-{end:%Y%m%d%H}'

    result, missing = {}, []
    for key in dict.fromkeys(_key(lat, lng) for lat, lng in coords):  # dedupe, keep order
        hit = cache.get(f'wx:{key[0]}:{key[1]}:{window}')
        if hit is not None:
            result[key] = hit
        else:
            missing.append(key)

    batches = [missing[i:i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for batch, forecasts in zip(batches, pool.map(lambda b: _fetch_batch(b, start, end), batches)):
            for key, forecast in zip(batch, forecasts):
                result[key] = forecast
                cache.set(f'wx:{key[0]}:{key[1]}:{window}', forecast, CACHE_TTL_S)
    return result


def lookup(forecasts, lat, lng):
    return forecasts[_key(lat, lng)]
