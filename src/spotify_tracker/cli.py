from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

from .analysis import run_sql, statistical_analysis, write_findings, write_json
from .pipeline import load_exports, normalize, load_genres, build_tables, build_aggregates


def build(args: argparse.Namespace) -> dict:
    root = args.project.resolve()
    processed = root / "data" / "processed"
    output = root / "output"
    processed.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    raw, audit = load_exports(args.input.resolve())
    events, quality = normalize(raw, args.timezone, round(args.min_play_seconds * 1000))
    del raw
    genres = load_genres(args.genres)
    tables = build_tables(events, genres)
    aggregates = build_aggregates(tables)
    for name, table in {**tables, **aggregates}.items():
        table.to_csv(processed / f"{name}.csv", index=False, encoding="utf-8")
    # A local editing template ordered by actual listening time, without invented genres.
    aggregates["top_artists"][["artist_name"]].assign(primary_genre="", source="").to_csv(output / "artist_genres_to_fill.csv", index=False)
    sql_results = run_sql(tables, root / "sql", processed / "spotify.sqlite", output / "sql")
    if not sql_results:
        raise ValueError("No analysis SQL files found; --project must point to this repository")
    # Reconcile independently computed SQL and Pandas totals and monthly metrics.
    check = sql_results["01_monthly_trends"].merge(aggregates["monthly_metrics"], on="month", suffixes=("_sql", "_pandas"), validate="one_to_one")
    for metric in ["listening_hours", "qualified_plays", "unique_artists", "unique_songs", "new_songs", "discovery_rate", "artist_loyalty"]:
        import numpy as np
        if not np.allclose(check[f"{metric}_sql"].fillna(0), check[f"{metric}_pandas"].fillna(0)):
            raise AssertionError(f"SQL/Pandas reconciliation failed: {metric}")
    fact = tables["fact_listening"]
    q = fact.loc[fact["qualified_play"].eq(1)]
    known = tables["dim_artists"].loc[tables["dim_artists"]["primary_genre"].ne("Unknown"), "artist_key"]
    total_minutes = float(fact["minutes_played"].sum())
    summary = {
        "start_date": tables["dim_dates"]["date"].min(), "end_date": tables["dim_dates"]["date"].max(),
        "timezone": args.timezone, "qualified_play_seconds": args.min_play_seconds,
        "music_events": len(fact), "qualified_plays": len(q),
        "listening_hours": total_minutes / 60,
        "unique_artists": int(q["artist_key"].nunique()), "unique_songs": int(q["track_key"].nunique()),
        "discovery_rate": float(fact["is_new_song"].sum() / len(q)),
        "artist_loyalty": float(fact["is_returning_artist"].sum() / len(q)),
        "genre_coverage": float(fact.loc[fact["artist_key"].isin(known), "minutes_played"].sum() / total_minutes) if total_minutes else 0,
        "sql_pandas_reconciliation": "passed", "data_quality": {**audit, **quality},
    }
    stats = statistical_analysis(aggregates["daily_metrics"], aggregates["monthly_metrics"])
    write_json(output / "summary.json", summary)
    write_json(output / "statistics.json", stats)
    sensitivity = []
    for seconds in [10, 30, 60]:
        candidate = fact.loc[fact["ms_played"].ge(seconds * 1000)]
        sensitivity.append({"threshold_seconds": seconds, "qualified_plays": len(candidate), "unique_artists": candidate["artist_key"].nunique(), "unique_songs": candidate["track_key"].nunique(), "discovery_rate": candidate["track_key"].nunique() / len(candidate) if len(candidate) else 0})
    pd.DataFrame(sensitivity).to_csv(output / "threshold_sensitivity.csv", index=False)
    write_findings(output / "findings.md", summary, stats, aggregates)
    from .powerbi import generate_project
    generate_project(root / "powerbi", processed)
    from .preview import generate_preview
    generate_preview(output, summary, aggregates)
    print(f"Analyzed {summary['music_events']:,} music events; {summary['listening_hours']:,.1f} hours; {summary['unique_artists']:,} artists; {summary['unique_songs']:,} songs.")
    print(f"SQL/Pandas reconciliation: passed. Genre coverage: {summary['genre_coverage']:.1%}.")
    print(f"Power BI: {root / 'powerbi' / 'SpotifyTracker.pbip'}")
    print(f"Findings: {output / 'findings.md'}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Spotify Extended Streaming History locally")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("build", help="Build CSVs, SQLite analysis, statistical findings, and Power BI project")
    command.add_argument("--input", type=Path, required=True, help="Folder containing Spotify Streaming_History_*.json exports")
    command.add_argument("--project", type=Path, default=Path.cwd(), help="Project repository root")
    command.add_argument("--timezone", default="America/Chicago")
    command.add_argument("--min-play-seconds", type=float, default=30)
    command.add_argument("--genres", type=Path, help="Verified artist_name,primary_genre,source CSV")
    enrichment = sub.add_parser("enrich-genres", help="Optional external lookup: sends artist names only to MusicBrainz; run build first")
    enrichment.add_argument("--project", type=Path, default=Path.cwd())
    enrichment.add_argument("--top", type=int, default=100, help="Look up this many leading artists; cached and limited to less than 1 request/second")
    args = parser.parse_args()
    try:
        if args.command == "enrich-genres":
            from .genres import enrich_genres
            enrich_genres(args.project.resolve(), args.top)
        else:
            build(args)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
