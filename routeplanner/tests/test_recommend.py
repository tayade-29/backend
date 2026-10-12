from django.test import SimpleTestCase

from routeplanner.services.recommend import explain, rank, summarize
from routeplanner.services.risk import Risk


def route(duration_s, *segments):
    """segments: (Risk, miles) pairs."""
    cps = [{'level': level, 'segment_miles': mi} for level, mi in segments]
    return summarize(cps, duration_s, sum(mi for _, mi in segments))


class SummarizeTests(SimpleTestCase):
    def test_counts_miles_per_level_and_weighted_average(self):
        s = route(3600, (Risk.LOW, 75), (Risk.HIGH, 25))
        self.assertEqual(s['high_miles'], 25)
        self.assertEqual(s['miles_by_level']['Low'], 75)
        self.assertAlmostEqual(s['avg_risk'], (0 * 75 + 2 * 25) / 100)
        self.assertEqual(s['max_risk'], Risk.HIGH)


class RankTests(SimpleTestCase):
    def test_fewer_severe_miles_beats_faster_route(self):
        fast_but_severe = route(3600, (Risk.LOW, 90), (Risk.SEVERE, 10))
        slow_but_safe = route(7200, (Risk.HIGH, 50), (Risk.LOW, 50))
        self.assertEqual(rank([fast_but_severe, slow_but_safe]), [1, 0])

    def test_severe_tie_broken_by_high_miles(self):
        a = route(3600, (Risk.SEVERE, 10), (Risk.HIGH, 30), (Risk.LOW, 60))
        b = route(5000, (Risk.SEVERE, 10), (Risk.HIGH, 5), (Risk.LOW, 85))
        self.assertEqual(rank([a, b]), [1, 0])

    def test_high_tie_broken_by_average_risk(self):
        a = route(3600, (Risk.MODERATE, 80), (Risk.LOW, 20))
        b = route(5000, (Risk.MODERATE, 10), (Risk.LOW, 90))
        self.assertEqual(rank([a, b]), [1, 0])

    def test_equal_risk_broken_by_duration(self):
        a = route(5000, (Risk.LOW, 100))
        b = route(3600, (Risk.LOW, 100))
        self.assertEqual(rank([a, b]), [1, 0])

    def test_no_travel_ranks_below_severe(self):
        no_travel = route(3600, (Risk.NO_TRAVEL, 5), (Risk.LOW, 95))
        severe = route(7200, (Risk.SEVERE, 40), (Risk.LOW, 60))
        self.assertEqual(rank([no_travel, severe]), [1, 0])

    def test_rounding_noise_does_not_decide(self):
        a = route(5000, (Risk.SEVERE, 25.0), (Risk.LOW, 75))
        b = route(3600, (Risk.SEVERE, 25.1), (Risk.LOW, 75))
        self.assertEqual(rank([a, b]), [1, 0])


class ExplainTests(SimpleTestCase):
    def test_names_the_deciding_criterion(self):
        best = route(7200, (Risk.HIGH, 10), (Risk.LOW, 90))
        other = route(3600, (Risk.SEVERE, 10), (Risk.LOW, 90))
        self.assertIn('fewer miles of Severe weather', explain(best, other))

    def test_falls_back_to_time(self):
        best = route(3600, (Risk.LOW, 100))
        other = route(5000, (Risk.LOW, 100))
        self.assertIn('fastest', explain(best, other))
