import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import numpy as np
import pandas as pd

from spotify_tracker.pipeline import load_exports, normalize, build_tables, build_aggregates, load_genres, stable_key
from spotify_tracker.analysis import run_sql, block_bootstrap_mean, statistical_analysis
from spotify_tracker.powerbi import make_model, generate_project


def record(ts="2024-01-01T23:00:00Z", ms=60000, artist="Artist A", song="Song A", **extra):
    return {"ts": ts, "ms_played": ms, "master_metadata_track_name": song, "master_metadata_album_artist_name": artist, "spotify_track_uri": "spotify:track:" + song.replace(" ", ""), **extra}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[1] / "tmp" / "tests"
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.test_root)
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.assertTrue(self.folder.resolve().is_relative_to(self.test_root.resolve()))
        self.temp.cleanup()

    def write(self, name, rows):
        (self.folder / name).write_text(json.dumps(rows), encoding="utf-8")

    def tables(self, rows, timezone="America/Chicago"):
        self.write("Streaming_History_Audio_2024.json", rows)
        raw, _ = load_exports(self.folder)
        clean, quality = normalize(raw, timezone, 30000)
        return build_tables(clean, load_genres(None)), quality

    def test_whole_file_duplicates_and_overlaps_preserve_multiplicity(self):
        a, b = record(), record(song="Song B")
        self.write("Streaming_History_Audio_2024.json", [a, a, b])
        self.write("Streaming_History_Audio_2024 - Copy.json", [a, a, b])
        self.write("Streaming_History_Audio_2024_1.json", [a, b])
        raw, audit = load_exports(self.folder)
        self.assertEqual(len(raw), 3)
        self.assertEqual(audit["raw_records"], 8)
        self.assertEqual(audit["duplicate_file_records_removed"], 3)
        self.assertEqual(audit["overlapping_records_removed"], 2)

    def test_video_is_not_deduplicated_against_audio(self):
        self.write("Streaming_History_Audio_2024.json", [record()])
        self.write("Streaming_History_Video_2024.json", [record()])
        raw, _ = load_exports(self.folder)
        self.assertEqual(len(raw), 2)

    def test_discovery_and_loyalty_use_qualified_plays_and_prior_dates(self):
        tables, _ = self.tables([
            record(ms=1000), record(ts="2024-01-02T01:00:00Z"),
            record(ts="2024-01-02T02:00:00Z"), record(ts="2024-01-02T23:00:00Z"),
            record(ts="2024-01-03T23:00:00Z", artist="Artist B", song="Song B"),
        ])
        fact = tables["fact_listening"]
        self.assertEqual(fact["qualified_play"].sum(), 4)
        self.assertEqual(fact["is_new_song"].sum(), 2)
        self.assertEqual(fact["is_new_artist"].sum(), 2)
        self.assertEqual(fact["is_returning_artist"].tolist(), [0, 0, 0, 1, 0])
        self.assertAlmostEqual(fact["minutes_played"].sum(), 4 + 1 / 60)

    def test_invalid_records_and_nonmusic_are_reported(self):
        tables, quality = self.tables([
            record(), record(ts="invalid"), record(ms=-1), record(ms=1.5),
            {"ts": "2024-01-02T12:00:00Z", "ms_played": 100000, "episode_name": "Podcast"},
        ])
        self.assertEqual(len(tables["fact_listening"]), 1)
        self.assertEqual(quality["invalid_records_removed"], 3)
        self.assertEqual(quality["content_type_records"]["podcast"], 1)

    def test_zero_days_and_partial_periods(self):
        tables, _ = self.tables([record(ts="2024-01-02T12:00:00Z"), record(ts="2024-03-02T12:00:00Z")])
        aggregates = build_aggregates(tables)
        february = aggregates["monthly_metrics"].set_index("month").loc["2024-02"]
        self.assertEqual(february["listening_hours"], 0)
        self.assertEqual(february["is_partial_month"], 0)
        self.assertEqual(aggregates["monthly_metrics"]["is_partial_month"].tolist(), [1, 0, 1])
        self.assertEqual(len(aggregates["daily_metrics"]), 61)

    def test_dst_conversion_and_unknown_skip_flags(self):
        tables, _ = self.tables([record(ts="2024-11-03T06:30:00Z", skipped=None), record(ts="2024-11-03T07:30:00Z", skipped=True)])
        fact = tables["fact_listening"]
        self.assertEqual(fact["hour"].tolist(), [1, 1])
        self.assertTrue(pd.isna(fact["skipped"].iloc[0]))
        self.assertEqual(fact["skipped"].iloc[1], 1)

    def test_sensitive_fields_never_exported(self):
        tables, _ = self.tables([record(ip_addr="192.0.2.1", username="private", incognito_mode=True)])
        for table in tables.values():
            self.assertNotIn("ip_addr", table.columns)
            self.assertNotIn("username", table.columns)
            self.assertNotIn("incognito_mode", table.columns)
            self.assertNotIn("192.0.2.1", table.to_csv(index=False))

    def test_verified_genre_mapping_and_duplicate_rejection(self):
        self.write("Streaming_History_Audio_2024.json", [record()])
        raw, _ = load_exports(self.folder)
        events, _ = normalize(raw, "UTC", 30000)
        genre_path = self.folder / "genres.csv"
        genre_path.write_text("artist_name,primary_genre,source\nArtist A,Rock,https://example.org/a\n")
        tables = build_tables(events, load_genres(genre_path))
        self.assertEqual(tables["dim_artists"]["primary_genre"].iloc[0], "Rock")
        genre_path.write_text("artist_name,primary_genre,source\nArtist A,Rock,x\nartist a,Pop,y\n")
        with self.assertRaises(ValueError): load_genres(genre_path)

    def test_sql_and_pandas_agree_with_zero_months(self):
        tables, _ = self.tables([record(), record(ts="2024-03-02T12:00:00Z"), record(ts="2024-03-02T12:05:00Z", artist="B", song="B")])
        root = Path(__file__).resolve().parents[1]
        sql = run_sql(tables, root / "sql", self.folder / "test.sqlite", self.folder / "sql")
        pandas = build_aggregates(tables)["monthly_metrics"]
        np.testing.assert_allclose(sql["01_monthly_trends"]["listening_hours"], pandas["listening_hours"])
        np.testing.assert_allclose(sql["01_monthly_trends"]["qualified_plays"], pandas["qualified_plays"])
        self.assertEqual(len(sql), 4)

    def test_artist_and_track_identity_is_stable(self):
        self.assertEqual(stable_key("a_", " Artist A "), stable_key("a_", "artist a"))
        tables, _ = self.tables([record(song="Same", artist="A", spotify_track_uri=""), record(song="Same", artist="B", spotify_track_uri="")])
        self.assertEqual(len(tables["dim_tracks"]), 2)

    def test_spotify_base62_ids_remain_case_sensitive(self):
        tables, _ = self.tables([record(spotify_track_uri="spotify:track:AbC"), record(spotify_track_uri="spotify:track:abc")])
        self.assertEqual(len(tables["dim_tracks"]), 2)
        self.assertEqual(tables["fact_listening"]["is_new_song"].sum(), 2)

    def test_constant_bootstrap_interval_and_minimum_size(self):
        self.assertEqual(block_bootstrap_mean(np.ones(20) * 5), (5, 5))
        with self.assertRaises(ValueError): block_bootstrap_mean(np.ones(3))

    def test_powerbi_references_existing_fields_and_measures(self):
        generate_project(self.folder / "powerbi", self.folder / "data")
        model = make_model(self.folder / "data")["model"]
        columns = {t["name"]: {c["name"] for c in t["columns"]} for t in model["tables"]}
        measures = {m["name"] for t in model["tables"] for m in t.get("measures", [])}
        visuals = list((self.folder / "powerbi").rglob("visual.json"))
        self.assertGreater(len(visuals), 30)
        for path in visuals:
            v = json.loads(path.read_text(encoding="utf-8"))
            p = v["position"]
            self.assertLessEqual(p["x"] + p["width"], 1280)
            self.assertLessEqual(p["y"] + p["height"], 900)
            for role in v["visual"]["query"]["queryState"].values():
                for projection in role["projections"]:
                    if "Measure" in projection["field"]:
                        self.assertIn(projection["field"]["Measure"]["Property"], measures)
                    else:
                        c = projection["field"]["Column"]
                        self.assertIn(c["Property"], columns[c["Expression"]["SourceRef"]["Entity"]])


if __name__ == "__main__":
    unittest.main()
