"""SQLite queries and reproducible statistical summaries of observed behavior."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd


def run_sql(tables: dict[str, pd.DataFrame], sql_dir: Path, database: Path, output: Path) -> dict[str, pd.DataFrame]:
    output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        for name, table in tables.items():
            table.to_sql(name, connection, if_exists="replace", index=False)
        connection.executescript("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_event ON fact_listening(event_id);
            CREATE INDEX IF NOT EXISTS idx_date ON fact_listening(date);
            CREATE INDEX IF NOT EXISTS idx_artist ON fact_listening(artist_key);
            CREATE INDEX IF NOT EXISTS idx_track ON fact_listening(track_key);
        """)
        results = {}
        for path in sorted(sql_dir.glob("*.sql")):
            result = pd.read_sql_query(path.read_text(encoding="utf-8"), connection)
            result.to_csv(output / f"{path.stem}.csv", index=False)
            results[path.stem] = result
    return results


def block_bootstrap_mean(values: np.ndarray, *, samples: int = 4000, block_size: int = 4, seed: int = 77) -> tuple[float, float]:
    """Circular moving-block bootstrap, preserving adjacent weeks within blocks."""
    values = np.asarray(values, dtype=float)
    if len(values) < 8:
        raise ValueError("At least eight complete weeks are required for a bootstrap interval")
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(values), size=(samples, int(np.ceil(len(values) / block_size))))
    indices = (starts[:, :, None] + np.arange(block_size)) % len(values)
    means = values[indices.reshape(samples, -1)[:, :len(values)]].mean(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def statistical_analysis(daily: pd.DataFrame, monthly: pd.DataFrame) -> dict:
    days = daily.copy()
    date = pd.to_datetime(days["date"])
    days["week_start"] = (date - pd.to_timedelta(date.dt.dayofweek, unit="D")).dt.strftime("%Y-%m-%d")
    complete_weeks = days.groupby("week_start").filter(lambda g: len(g) == 7)
    weekday = complete_weeks.loc[complete_weeks["is_weekend"].eq(0)].groupby("week_start")["listening_minutes"].mean()
    weekend = complete_weeks.loc[complete_weeks["is_weekend"].eq(1)].groupby("week_start")["listening_minutes"].mean()
    difference = (weekend - weekday).dropna().to_numpy()
    result = {
        "method": "Paired complete-week weekend-minus-weekday mean minutes/day; circular moving-block bootstrap of four adjacent weeks; 4,000 replicates; seed 77.",
        "complete_weeks": len(difference),
        "caveats": [
            "One person's observational history; associations do not establish causation or population effects.",
            "Zero-listening dates are included. Missing export coverage cannot be distinguished from inactivity.",
            "Four-week blocks account for some serial dependence; longer-term dependence and selection effects remain.",
            "All dates use the configured timezone and playback-end timestamps. Historical travel is not inferred.",
            "Partial boundary months are excluded from month-over-month extremes and the trend slope.",
        ],
    }
    if len(difference) >= 8:
        low, high = block_bootstrap_mean(difference)
        result["weekend_vs_weekday"] = {
            "weekday_mean_minutes": float(weekday.mean()),
            "weekend_mean_minutes": float(weekend.mean()),
            "difference_minutes_per_day": float(difference.mean()),
            "bootstrap_95pct_low": low,
            "bootstrap_95pct_high": high,
            "interval_excludes_zero": bool(low > 0 or high < 0),
        }
    complete = monthly.loc[monthly["is_partial_month"].eq(0)].copy()
    if len(complete) >= 2:
        x = pd.to_datetime(complete["month"] + "-01")
        month_index = (x.dt.year * 12 + x.dt.month).to_numpy()
        y = complete["listening_hours"].to_numpy()
        result["monthly_trend"] = {
            "complete_months": len(complete),
            "ols_slope_hours_per_month": float(np.polyfit(month_index - month_index.min(), y, 1)[0]),
            "lag_one_autocorrelation": float(pd.Series(y).autocorr()) if np.std(y) > 0 and len(y) > 2 else None,
            "interpretation": "Descriptive linear slope, not a forecast or a significance test.",
        }
        # Differences use consecutive full months only.
        delta = complete["listening_hours"].diff()
        delta.loc[pd.Series(month_index, index=complete.index).diff().ne(1)] = np.nan
        if delta.notna().any():
            largest = delta.abs().idxmax()
            result["largest_monthly_change"] = {"month": str(complete.loc[largest, "month"]), "change_hours": float(delta.loc[largest]), "interpretation": "Exploratory largest absolute change; no multiple-comparison significance claim."}
    return result


def write_findings(path: Path, summary: dict, stats: dict, aggregates: dict[str, pd.DataFrame]) -> None:
    monthly = aggregates["monthly_metrics"]
    top = aggregates["top_artists"].iloc[0]
    lines = [
        "# Listening history findings", "",
        f"Observed playback-end dates: **{summary['start_date']} to {summary['end_date']}** ({summary['timezone']}).", "",
        f"- {summary['music_events']:,} valid music events; {summary['qualified_plays']:,} qualified plays at {summary['qualified_play_seconds']:g}+ seconds.",
        f"- {summary['listening_hours']:,.1f} music listening hours, including short plays.",
        f"- {summary['unique_artists']:,} artists and {summary['unique_songs']:,} songs with qualifying plays.",
        f"- Discovery rate: {summary['discovery_rate']:.1%}; artist loyalty: {summary['artist_loyalty']:.1%}.",
        f"- Most listening time: **{top['artist_name']}**, {top['listening_hours']:,.1f} hours.",
        f"- Genre coverage: {summary['genre_coverage']:.1%} of listening minutes. Unmapped artists remain Unknown.", "",
        "## Statistical analysis", "",
    ]
    if "weekend_vs_weekday" in stats:
        s = stats["weekend_vs_weekday"]
        lines += [f"Across {stats['complete_weeks']} complete weeks, weekend listening averaged {s['weekend_mean_minutes']:.1f} minutes/day versus {s['weekday_mean_minutes']:.1f} on weekdays. The paired difference was {s['difference_minutes_per_day']:+.1f} minutes/day (95% moving-block bootstrap interval: {s['bootstrap_95pct_low']:+.1f} to {s['bootstrap_95pct_high']:+.1f}).", ""]
    if "monthly_trend" in stats:
        s = stats["monthly_trend"]
        lines += [f"The descriptive linear trend across {s['complete_months']} complete months was {s['ols_slope_hours_per_month']:+.2f} listening hours per month. This is not a forecast or causal estimate.", ""]
    if "largest_monthly_change" in stats:
        s = stats["largest_monthly_change"]
        lines += [f"The largest absolute change between adjacent complete months occurred in {s['month']}: {s['change_hours']:+.1f} hours. This was selected retrospectively and is exploratory.", ""]
    lines += ["## Definitions and limitations", "", "Discovery means first observed qualifying play of a song in this export, not the song's release or the listener's first lifetime exposure. Discovery rate is first-observed song events divided by qualified plays. Artist loyalty is the fraction of qualified plays to artists first heard on an earlier local date. The first observed day for each artist is excluded from loyalty's numerator.", "", "Whole-file duplicates and overlapping records are removed. Nonmusic records are excluded from music metrics. First/last years and months may be incomplete; do not compare their totals as complete periods.", "", *[f"- {c}" for c in stats["caveats"]], "", "## Reproduce", "", "See the project README for the pipeline command. SQL result tables are in `output/sql/`; the local database is `data/processed/spotify.sqlite`. Statistical details are in `output/statistics.json`.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
