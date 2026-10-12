from django.test import SimpleTestCase

from routeplanner.services.risk import (
    Risk, assess, rain_risk, severity_score, snow_risk, wind_risk, wind_risk_for_load,
)


class WindThresholdTests(SimpleTestCase):
    def test_bands_and_boundaries(self):
        cases = [
            (0, Risk.LOW), (24.9, Risk.LOW),
            (25, Risk.MODERATE), (34.9, Risk.MODERATE),
            (35, Risk.HIGH), (44.9, Risk.HIGH),
            (45, Risk.SEVERE), (54.9, Risk.SEVERE),
            (55, Risk.NO_TRAVEL), (80, Risk.NO_TRAVEL),
        ]
        for mph, expected in cases:
            with self.subTest(mph=mph):
                self.assertEqual(wind_risk(mph), expected)


class RainThresholdTests(SimpleTestCase):
    def test_bands_and_boundaries(self):
        cases = [
            (0, Risk.LOW), (0.09, Risk.LOW),
            (0.10, Risk.MODERATE), (0.24, Risk.MODERATE),
            (0.25, Risk.HIGH), (0.49, Risk.HIGH),
            (0.50, Risk.SEVERE), (1.00, Risk.SEVERE),
            (1.01, Risk.NO_TRAVEL),
        ]
        for rate, expected in cases:
            with self.subTest(rate=rate):
                self.assertEqual(rain_risk(rate), expected)


class SnowThresholdTests(SimpleTestCase):
    def test_bands_and_boundaries(self):
        cases = [
            (0, Risk.LOW), (0.49, Risk.LOW),
            (0.5, Risk.MODERATE), (0.99, Risk.MODERATE),
            (1.0, Risk.HIGH), (1.99, Risk.HIGH),
            (2.0, Risk.SEVERE), (3.0, Risk.SEVERE),
            (3.01, Risk.NO_TRAVEL),
        ]
        for rate, expected in cases:
            with self.subTest(rate=rate):
                self.assertEqual(snow_risk(rate), expected)


class LoadRuleTests(SimpleTestCase):
    def test_55_mph_is_no_travel_for_any_load(self):
        self.assertEqual(wind_risk_for_load(55, 0), Risk.NO_TRAVEL)

    def test_45_to_54_mph_over_30k_lb_is_no_travel(self):
        self.assertEqual(wind_risk_for_load(45, 30_001), Risk.NO_TRAVEL)
        self.assertEqual(wind_risk_for_load(54.9, 45_000), Risk.NO_TRAVEL)

    def test_45_to_54_mph_at_or_under_30k_lb_stays_severe(self):
        self.assertEqual(wind_risk_for_load(50, 30_000), Risk.SEVERE)

    def test_35_to_44_mph_over_40k_lb_is_severe(self):
        self.assertEqual(wind_risk_for_load(35, 40_001), Risk.SEVERE)
        self.assertEqual(wind_risk_for_load(44.9, 80_000), Risk.SEVERE)

    def test_35_to_44_mph_at_or_under_40k_lb_stays_high(self):
        self.assertEqual(wind_risk_for_load(40, 40_000), Risk.HIGH)

    def test_load_does_not_affect_lower_winds(self):
        self.assertEqual(wind_risk_for_load(30, 80_000), Risk.MODERATE)


class SeverityScoreTests(SimpleTestCase):
    def test_floor_of_score_always_matches_level(self):
        import random
        rng = random.Random(42)
        for _ in range(2000):
            wind = rng.uniform(0, 80)
            rain = rng.choice([0, rng.uniform(0, 1.5), 0.25, 1.0])
            snow = rng.choice([0, rng.uniform(0, 4), 2.0, 3.0])
            load = rng.choice([10_000, 30_000, 35_000, 45_000])
            score = severity_score(wind, rain, snow, load)
            level = assess(wind, rain, snow, load).level
            with self.subTest(wind=wind, rain=rain, snow=snow, load=load):
                self.assertEqual(int(score), level)

    def test_calm_but_breezy_shows_some_signal(self):
        self.assertAlmostEqual(severity_score(12.5, 0, 0, 20_000), 0.5)


class AssessTests(SimpleTestCase):
    def test_overall_is_worst_factor(self):
        result = assess(wind_mph=10, rain_in_hr=0.3, snow_in_hr=0.6, load_lb=20_000)
        self.assertEqual(result.level, Risk.HIGH)
        self.assertEqual(result.reasons, ('rain 0.30 in/hr',))

    def test_load_rule_reason_mentions_weight(self):
        result = assess(wind_mph=40, rain_in_hr=0, snow_in_hr=0, load_lb=42_000)
        self.assertEqual(result.level, Risk.SEVERE)
        self.assertIn('42,000 lb', result.reasons[0])

    def test_calm_weather_is_low_with_no_reasons(self):
        result = assess(wind_mph=5, rain_in_hr=0, snow_in_hr=0, load_lb=80_000)
        self.assertEqual(result.level, Risk.LOW)
        self.assertEqual(result.reasons, ())
