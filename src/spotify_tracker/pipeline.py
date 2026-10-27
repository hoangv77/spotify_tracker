"""Load private exports, validate records, and construct an analytical star schema."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unicodedata

import numpy as np
import pandas as pd


def stable_key(prefix: str, *parts: str, case_sensitive: bool = False) -> str:
    normalized = [unicodedata.normalize("NFKC", str(p)).strip() for p in parts]
    if not case_sensitive:
        normalized = [p.casefold() for p in normalized]
    return prefix + hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode()).hexdigest()[:20]


def load_exports(folder: Path) -> tuple[pd.DataFrame, dict]:
    paths = sorted(folder.glob("Streaming_History_*.json"), key=lambda p: ("copy" in p.stem.lower(), p.name))
    if not paths:
        raise ValueError(f"No Streaming_History_*.json exports found in {folder}")
    frames, hashes, manifest = [], {}, []
    raw_count = 0
    duplicate_file_rows = 0
    for path in paths:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        stream_type = "video" if "_Video_" in path.name else "audio"
        file_key = (stream_type, digest)
        records = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
            raise ValueError(f"Expected a JSON array of records: {path.name}")
        raw_count += len(records)
        entry = {"file": path.name, "sha256": digest, "records": len(records)}
        if file_key in hashes:
            entry["duplicate_of"] = hashes[file_key]
            duplicate_file_rows += len(records)
            manifest.append(entry)
            continue
        hashes[file_key] = path.name
        manifest.append(entry)
        if not records:
            continue
        frame = pd.DataFrame(records)
        # Full-record signatures are used only in memory. IPs never reach output.
        frame["_signature"] = [hashlib.sha256(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest() for r in records]
        frame["_stream_type"] = stream_type
        frame["_source_file"] = path.name
        frame["_occurrence"] = frame.groupby("_signature").cumcount()
        frames.append(frame)
    if not frames:
        raise ValueError("The export files contain no records")
    data = pd.concat(frames, ignore_index=True)
    before = len(data)
    # Match duplicate occurrences across overlapping files, retaining multiplicity
    # inside a file and keeping audio and video as distinct event types.
    data = data.drop_duplicates(["_stream_type", "_signature", "_occurrence"]).copy()
    return data, {
        "files": manifest,
        "raw_records": raw_count,
        "duplicate_file_records_removed": duplicate_file_rows,
        "overlapping_records_removed": before - len(data),
        "deduplicated_records": len(data),
    }


def normalize(data: pd.DataFrame, timezone: str, threshold_ms: int) -> tuple[pd.DataFrame, dict]:
    if threshold_ms < 1:
        raise ValueError("The qualified-play threshold must be at least 1 millisecond")
    if not {"ts", "ms_played"}.issubset(data.columns):
        raise ValueError("Export records require ts and ms_played")
    data = data.copy()
    timestamps = pd.to_datetime(data["ts"], utc=True, errors="coerce")
    durations = pd.to_numeric(data["ms_played"], errors="coerce")
    valid_duration = durations.notna() & np.isfinite(durations) & durations.ge(0) & durations.mod(1).eq(0) & durations.le(np.iinfo(np.int64).max)
    valid = timestamps.notna() & valid_duration
    quality = {
        "invalid_timestamp_records": int(timestamps.isna().sum()),
        "invalid_duration_records": int((~valid_duration).sum()),
        "invalid_records_removed": int((~valid).sum()),
    }
    clean = data.loc[valid].copy()
    clean["ended_at_utc"] = timestamps.loc[valid]
    clean["ms_played"] = durations.loc[valid].astype("int64")
    if clean.empty:
        raise ValueError("No records with valid timestamps and nonnegative durations")
    local = clean["ended_at_utc"].dt.tz_convert(timezone)
    clean["date"] = local.dt.strftime("%Y-%m-%d")
    clean["hour"] = local.dt.hour
    clean["year"] = local.dt.year
    clean["month"] = local.dt.strftime("%Y-%m")
    clean["weekday_number"] = local.dt.dayofweek
    clean["weekday"] = local.dt.day_name()
    clean["minutes_played"] = clean["ms_played"] / 60000
    clean["qualified_play"] = clean["ms_played"].ge(threshold_ms).astype(int)
    for name in ["master_metadata_track_name", "master_metadata_album_artist_name", "master_metadata_album_album_name", "spotify_track_uri", "episode_name", "spotify_episode_uri", "audiobook_title", "audiobook_uri", "reason_end", "platform"]:
        if name not in clean:
            clean[name] = ""
        clean[name] = clean[name].fillna("").astype(str).str.strip()
    clean["content_type"] = np.select(
        [clean["spotify_track_uri"].str.startswith("spotify:track:") | clean["master_metadata_track_name"].ne(""),
         clean["spotify_episode_uri"].ne("") | clean["episode_name"].ne(""),
         clean["audiobook_uri"].ne("") | clean["audiobook_title"].ne("")],
        ["music", "podcast", "audiobook"], default="unknown")
    clean["artist_name"] = clean["master_metadata_album_artist_name"].replace("", "Unknown artist")
    clean["track_name"] = clean["master_metadata_track_name"].replace("", "Unknown track")
    clean["album_name"] = clean["master_metadata_album_album_name"]
    clean["artist_key"] = clean["artist_name"].map(lambda s: stable_key("a_", s))
    # Spotify base-62 IDs are case-sensitive, unlike normalized display names.
    clean["track_key"] = [stable_key("t_", uri, case_sensitive=True) if uri else stable_key("t_", artist, title, album) for uri, artist, title, album in zip(clean["spotify_track_uri"], clean["artist_name"], clean["track_name"], clean["album_name"])]
    # Legacy null skip flags stay null; reason_end is retained separately.
    skipped = clean.get("skipped", pd.Series(pd.NA, index=clean.index))
    clean["skipped"] = skipped.map(lambda v: int(v) if isinstance(v, (bool, np.bool_)) else pd.NA).astype("Int64")
    clean["stream_type"] = clean["_stream_type"]
    clean["event_id"] = [stable_key("e_", kind, signature, str(occ)) for kind, signature, occ in zip(clean["_stream_type"], clean["_signature"], clean["_occurrence"])]
    clean = clean.sort_values(["ended_at_utc", "event_id"]).reset_index(drop=True)
    quality["content_type_records"] = {str(k): int(v) for k, v in clean["content_type"].value_counts().items()}
    quality["valid_records"] = len(clean)
    return clean, quality


def load_genres(path: Path | None) -> pd.DataFrame:
    columns = ["artist_name", "primary_genre", "source"]
    if path is None:
        return pd.DataFrame(columns=columns)
    mapping = pd.read_csv(path, dtype=str).fillna("")
    if not set(columns).issubset(mapping.columns):
        raise ValueError("Genre CSV must contain artist_name, primary_genre, source")
    mapping = mapping[columns].apply(lambda c: c.str.strip())
    if mapping.eq("").any().any():
        raise ValueError("Every genre mapping requires a nonblank artist, primary genre, and source")
    mapping["artist_key"] = mapping["artist_name"].map(lambda n: stable_key("a_", n))
    if mapping["artist_key"].duplicated().any():
        raise ValueError("Genre CSV must contain exactly one primary genre per artist")
    return mapping


def build_tables(events: pd.DataFrame, genres: pd.DataFrame) -> dict[str, pd.DataFrame]:
    music = events.loc[events["content_type"].eq("music")].copy()
    if music.empty or not music["qualified_play"].any():
        raise ValueError("No qualifying music plays found")
    qualified = music.loc[music["qualified_play"].eq(1)]
    first_artist = qualified.groupby("artist_key")["date"].min()
    first_song = qualified.groupby("track_key")["date"].min()
    music["artist_first_heard_date"] = music["artist_key"].map(first_artist)
    music["song_first_heard_date"] = music["track_key"].map(first_song)
    music["is_new_artist"] = 0
    music["is_new_song"] = 0
    music.loc[qualified.drop_duplicates("artist_key").index, "is_new_artist"] = 1
    music.loc[qualified.drop_duplicates("track_key").index, "is_new_song"] = 1
    music["is_returning_artist"] = (music["qualified_play"].eq(1) & music["date"].gt(music["artist_first_heard_date"].fillna("9999"))).astype(int)
    artists = music[["artist_key", "artist_name"]].drop_duplicates("artist_key").sort_values("artist_name")
    artists["first_heard_date"] = artists["artist_key"].map(first_artist)
    if not genres.empty:
        artists = artists.merge(genres[["artist_key", "primary_genre", "source"]], on="artist_key", how="left", validate="one_to_one")
    else:
        artists["primary_genre"] = "Unknown"
        artists["source"] = "Not in export"
    artists["primary_genre"] = artists["primary_genre"].fillna("Unknown")
    artists["source"] = artists["source"].fillna("Not mapped")
    tracks = music[["track_key", "track_name", "artist_key", "album_name", "spotify_track_uri"]].drop_duplicates("track_key")
    tracks["first_heard_date"] = tracks["track_key"].map(first_song)
    track_artists = artists.set_index("artist_key")["artist_name"]
    tracks["track_label"] = tracks["track_name"] + " - " + tracks["artist_key"].map(track_artists) + " [" + tracks["track_key"].str[-6:] + "]"
    # Use the entire observed export interval, including zero-listening days.
    dates = pd.date_range(events["date"].min(), events["date"].max())
    calendar = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "year": dates.year, "month": dates.strftime("%Y-%m"), "month_number": dates.month, "weekday_number": dates.dayofweek, "weekday": dates.day_name(), "is_weekend": (dates.dayofweek >= 5).astype(int)})
    fact_columns = ["event_id", "ended_at_utc", "date", "hour", "weekday_number", "weekday", "year", "month", "artist_key", "track_key", "ms_played", "minutes_played", "qualified_play", "is_new_artist", "is_new_song", "is_returning_artist", "skipped", "reason_end", "platform", "stream_type"]
    fact = music[fact_columns].copy()
    fact["ended_at_utc"] = fact["ended_at_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"fact_listening": fact, "dim_artists": artists.reset_index(drop=True), "dim_tracks": tracks.reset_index(drop=True), "dim_dates": calendar}


def build_aggregates(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    fact = tables["fact_listening"]
    qualified = fact.loc[fact["qualified_play"].eq(1)]
    def summarize(group: pd.DataFrame) -> pd.Series:
        q = group.loc[group["qualified_play"].eq(1)]
        artist_minutes = group.groupby("artist_key")["minutes_played"].sum()
        minutes = group["minutes_played"].sum()
        return pd.Series({"listening_hours": minutes / 60, "events": len(group), "qualified_plays": len(q), "unique_artists": q["artist_key"].nunique(), "unique_songs": q["track_key"].nunique(), "new_artists": int(group["is_new_artist"].sum()), "new_songs": int(group["is_new_song"].sum()), "discovery_rate": group["is_new_song"].sum() / len(q) if len(q) else 0, "artist_loyalty": group["is_returning_artist"].sum() / len(q) if len(q) else 0, "top5_artist_share": artist_minutes.nlargest(5).sum() / minutes if minutes else 0})
    monthly = fact.groupby("month").apply(summarize, include_groups=False).reset_index()
    all_months = pd.DataFrame({"month": tables["dim_dates"]["month"].drop_duplicates()})
    monthly = all_months.merge(monthly, on="month", how="left").fillna(0)
    observed_min, observed_max = tables["dim_dates"]["date"].min(), tables["dim_dates"]["date"].max()
    monthly["is_partial_month"] = [int(pd.Timestamp(m + "-01").strftime("%Y-%m-%d") < observed_min or (pd.Timestamp(m + "-01") + pd.offsets.MonthEnd()).strftime("%Y-%m-%d") > observed_max) for m in monthly["month"]]
    yearly = fact.groupby("year").apply(summarize, include_groups=False).reset_index()
    yearly["is_partial_year"] = [int(f"{y}-01-01" < observed_min or f"{y}-12-31" > observed_max) for y in yearly["year"]]
    daily = fact.groupby("date").agg(listening_minutes=("minutes_played", "sum"), qualified_plays=("qualified_play", "sum"), new_songs=("is_new_song", "sum"), new_artists=("is_new_artist", "sum")).reset_index()
    daily = tables["dim_dates"].merge(daily, on="date", how="left").fillna(0)
    hourly = fact.groupby(["weekday_number", "weekday", "hour"]).agg(listening_hours=("minutes_played", lambda s: s.sum() / 60), qualified_plays=("qualified_play", "sum")).reset_index()
    top_artists = fact.groupby("artist_key").agg(listening_hours=("minutes_played", lambda s: s.sum() / 60), qualified_plays=("qualified_play", "sum"), new_songs=("is_new_song", "sum")).reset_index().merge(tables["dim_artists"], on="artist_key", validate="one_to_one").sort_values("listening_hours", ascending=False)
    top_tracks = fact.groupby("track_key").agg(listening_hours=("minutes_played", lambda s: s.sum() / 60), qualified_plays=("qualified_play", "sum")).reset_index().merge(tables["dim_tracks"], on="track_key", validate="one_to_one").merge(tables["dim_artists"][["artist_key", "artist_name"]], on="artist_key", validate="many_to_one").sort_values("listening_hours", ascending=False)
    genre = fact.merge(tables["dim_artists"][["artist_key", "primary_genre"]], on="artist_key", validate="many_to_one").groupby(["month", "primary_genre"]).agg(listening_hours=("minutes_played", lambda s: s.sum() / 60), qualified_plays=("qualified_play", "sum")).reset_index()
    assert int(monthly["qualified_plays"].sum()) == len(qualified)
    return {"monthly_metrics": monthly, "yearly_metrics": yearly, "daily_metrics": daily, "hourly_patterns": hourly, "top_artists": top_artists, "top_tracks": top_tracks, "genre_trends": genre}
