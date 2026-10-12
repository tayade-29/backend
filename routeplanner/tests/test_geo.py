from django.test import SimpleTestCase

from routeplanner.services.geo import (
    decode_polyline, haversine_miles, sample_checkpoints,
)

# A straight line due north: 1 degree of latitude is ~69.09 miles.
MILES_PER_DEG_LAT = 69.09


def northbound_line(miles, steps=100):
    deg = miles / MILES_PER_DEG_LAT
    return [(i * deg / steps, -100.0) for i in range(steps + 1)]


class PolylineTests(SimpleTestCase):
    def test_decodes_google_reference_example(self):
        # Example from Google's polyline algorithm documentation.
        points = decode_polyline('_p~iF~ps|U_ulLnnqC_mqNvxq`@')
        self.assertEqual(points, [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])


class HaversineTests(SimpleTestCase):
    def test_one_degree_latitude(self):
        self.assertAlmostEqual(haversine_miles((0, 0), (1, 0)), MILES_PER_DEG_LAT, delta=0.1)

    def test_same_point_is_zero(self):
        self.assertEqual(haversine_miles((35, -97), (35, -97)), 0)


class SampleCheckpointTests(SimpleTestCase):
    def setUp(self):
        self.points = northbound_line(100)
        # 100 miles in 2 hours at constant speed.
        self.knots = [(0, 0), (100, 7200)]

    def test_places_checkpoint_every_interval_plus_start(self):
        cps = sample_checkpoints(self.points, 25, self.knots)
        self.assertEqual([round(c['mile']) for c in cps], [0, 25, 50, 75, 100])

    def test_adds_destination_when_not_a_multiple(self):
        cps = sample_checkpoints(northbound_line(60), 25, [(0, 0), (60, 3600)])
        self.assertEqual([round(c['mile']) for c in cps], [0, 25, 50, 60])

    def test_segment_miles_sum_to_route_length(self):
        for interval in (10, 25, 50):
            with self.subTest(interval=interval):
                cps = sample_checkpoints(self.points, interval, self.knots)
                self.assertAlmostEqual(sum(c['segment_miles'] for c in cps), 100, delta=0.5)

    def test_eta_is_interpolated_from_time_knots(self):
        cps = sample_checkpoints(self.points, 50, self.knots)
        self.assertAlmostEqual(cps[1]['offset_s'], 3600, delta=30)

    def test_eta_follows_uneven_speeds(self):
        # First 20 miles slow (city), remaining 80 fast (highway).
        knots = [(0, 0), (20, 3600), (100, 7200)]
        cps = sample_checkpoints(self.points, 10, knots)
        at_10 = next(c for c in cps if round(c['mile']) == 10)
        at_60 = next(c for c in cps if round(c['mile']) == 60)
        self.assertAlmostEqual(at_10['offset_s'], 1800, delta=30)
        self.assertAlmostEqual(at_60['offset_s'], 5400, delta=30)
