import unittest

import numpy as np
import pandas as pd

from spotify_tracker.genres import exact_artist_match, select_genre
from spotify_tracker.analysis import statistical_analysis


class GenreAndStatisticsTests(unittest.TestCase):
    def test_ambiguous_artist_names_are_not_guessed(self):
        artists = [{"id": "one", "name": "Common Name", "score": 100}, {"id": "two", "name": "Common Name", "score": 100}]
        self.assertIsNone(exact_artist_match(artists, "Common Name"))
        self.assertIsNone(exact_artist_match([{"id": "one", "name": "Common Name", "score": 94}], "Common Name"))

    def test_exact_alias_and_minimum_score(self):
        artist = {"id": "one", "name": "Canonical", "score": 95, "aliases": [{"name": "Alias"}]}
        self.assertEqual(exact_artist_match([artist], " ALIAS ")["id"], "one")
        self.assertIsNone(exact_artist_match([artist], "Different artist"))

    def test_genre_selection_requires_positive_votes(self):
        self.assertIsNone(select_genre([{"name": "rock", "count": 0}]))
        self.assertEqual(select_genre([{"name": "rock", "count": 1}, {"name": "jazz", "count": 2}]), "jazz")
        self.assertEqual(select_genre([{"name": "rock", "count": 2}, {"name": "jazz", "count": 2}]), "jazz")

    def test_paired_week_comparison_recovers_known_behavior(self):
        dates = pd.date_range("2024-01-01", periods=7 * 20)
        daily = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "is_weekend": (dates.dayofweek >= 5).astype(int), "listening_minutes": np.where(dates.dayofweek >= 5, 90.0, 60.0)})
        monthly = pd.DataFrame({"month": ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05"], "listening_hours": [1, 2, 3, 4, 1000], "is_partial_month": [0, 0, 0, 0, 1]})
        result = statistical_analysis(daily, monthly)
        self.assertEqual(result["complete_weeks"], 20)
        self.assertEqual(result["weekend_vs_weekday"]["difference_minutes_per_day"], 30)
        self.assertEqual(result["weekend_vs_weekday"]["bootstrap_95pct_low"], 30)
        self.assertAlmostEqual(result["monthly_trend"]["ols_slope_hours_per_month"], 1)


if __name__ == "__main__":
    unittest.main()
