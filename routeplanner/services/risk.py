"""
Weather risk classification for a single checkpoint.

Pure functions only: numbers in, risk level out. No Django, no HTTP.

Boundary interpretation (the spec's ranges overlap at the edges, e.g. rain
"0.10-0.25" and "0.25-0.50"):
  * Each band is half-open [lower, upper): a value equal to a boundary
    belongs to the higher band.
  * The top boundary is the exception and follows the spec's wording:
      wind  ">= 55 mph"   -> 55.0 is No Travel
      rain  "> 1.00 in/hr" -> 1.00 is still Severe
      snow  "> 3.0 in/hr"  -> 3.0 is still Severe
"""
from dataclasses import dataclass
from enum import IntEnum


class Risk(IntEnum):
    # IntEnum so levels can be compared (max(), >=) and averaged as numbers.
    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self):
        return self.name.replace('_', ' ').title()


# Upper bounds of the Low, Moderate, High and Severe bands.
WIND_MPH = (25, 35, 45, 55)
RAIN_IN_PER_HR = (0.10, 0.25, 0.50, 1.00)
SNOW_IN_PER_HR = (0.5, 1.0, 2.0, 3.0)

# Load rules (pounds).
NO_TRAVEL_WIND_WEIGHT_LB = 30_000  # 45-54 mph + > 30,000 lb -> No Travel
SEVERE_WIND_WEIGHT_LB = 40_000     # 35-44 mph + > 40,000 lb -> Severe


def _band(value, bounds, severe_includes_upper):
    low, moderate, high, severe = bounds
    if value < low:
        return Risk.LOW
    if value < moderate:
        return Risk.MODERATE
    if value < high:
        return Risk.HIGH
    if value < severe or (severe_includes_upper and value == severe):
        return Risk.SEVERE
    return Risk.NO_TRAVEL


def wind_risk(wind_mph):
    return _band(wind_mph, WIND_MPH, severe_includes_upper=False)


def rain_risk(rain_in_hr):
    return _band(rain_in_hr, RAIN_IN_PER_HR, severe_includes_upper=True)


def snow_risk(snow_in_hr):
    return _band(snow_in_hr, SNOW_IN_PER_HR, severe_includes_upper=True)


def wind_risk_for_load(wind_mph, load_lb):
    """Wind risk after applying the load rules. Heavier loads only ever escalate."""
    base = wind_risk(wind_mph)
    if base == Risk.SEVERE and load_lb > NO_TRAVEL_WIND_WEIGHT_LB:
        return Risk.NO_TRAVEL
    if base == Risk.HIGH and load_lb > SEVERE_WIND_WEIGHT_LB:
        return Risk.SEVERE
    return base


def _band_position(value, bounds, level):
    """`level` plus how far `value` sits inside that band, in [level, level + 1)."""
    lowers = (0, *bounds)
    if level == Risk.NO_TRAVEL:
        top = bounds[-1]
        fraction = (value - top) / top
    else:
        lower, upper = lowers[level], lowers[level + 1]
        fraction = (value - lower) / (upper - lower)
    return level + min(max(fraction, 0.0), 0.99)


def severity_score(wind_mph, rain_in_hr, snow_in_hr, load_lb):
    """Continuous 0-5 score for heatmap shading. floor(score) == assess(...).level.

    Plain levels would paint a calm day as a blank map; the fraction lets the
    heatmap show conditions building up inside a band.
    """
    wind = wind_risk_for_load(wind_mph, load_lb)
    if wind == wind_risk(wind_mph):
        wind_score = _band_position(wind_mph, WIND_MPH, wind)
    else:
        # Escalated by load: keep the escalated level, use the original band's fraction.
        wind_score = wind + _band_position(wind_mph, WIND_MPH, wind_risk(wind_mph)) % 1
    return max(
        wind_score,
        _band_position(rain_in_hr, RAIN_IN_PER_HR, rain_risk(rain_in_hr)),
        _band_position(snow_in_hr, SNOW_IN_PER_HR, snow_risk(snow_in_hr)),
    )


@dataclass(frozen=True)
class CheckpointRisk:
    level: Risk
    wind: Risk
    rain: Risk
    snow: Risk
    reasons: tuple  # human-readable drivers of `level`, for the UI


def assess(wind_mph, rain_in_hr, snow_in_hr, load_lb):
    """Overall risk = worst of wind (load-adjusted), rain and snow."""
    wind = wind_risk_for_load(wind_mph, load_lb)
    rain = rain_risk(rain_in_hr)
    snow = snow_risk(snow_in_hr)
    level = max(wind, rain, snow)

    reasons = []
    if level > Risk.LOW:
        if wind == level:
            note = f'wind {wind_mph:.0f} mph'
            if wind != wind_risk(wind_mph):
                note += f' with {load_lb:,.0f} lb load'
            reasons.append(note)
        if rain == level:
            reasons.append(f'rain {rain_in_hr:.2f} in/hr')
        if snow == level:
            reasons.append(f'snow {snow_in_hr:.2f} in/hr')

    return CheckpointRisk(level=level, wind=wind, rain=rain, snow=snow, reasons=tuple(reasons))
