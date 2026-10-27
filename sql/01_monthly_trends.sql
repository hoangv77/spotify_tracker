-- Qualified plays use the configured threshold; listening hours include short plays.
WITH monthly AS (
    SELECT d.month,
           COALESCE(SUM(f.ms_played) / 3600000.0, 0) AS listening_hours,
           COALESCE(SUM(f.qualified_play), 0) AS qualified_plays,
           COUNT(DISTINCT CASE WHEN f.qualified_play = 1 THEN f.artist_key END) AS unique_artists,
           COUNT(DISTINCT CASE WHEN f.qualified_play = 1 THEN f.track_key END) AS unique_songs,
           COALESCE(SUM(f.is_new_song), 0) AS new_songs,
           SUM(f.is_new_song) * 1.0 / NULLIF(SUM(f.qualified_play), 0) AS discovery_rate,
           SUM(f.is_returning_artist) * 1.0 / NULLIF(SUM(f.qualified_play), 0) AS artist_loyalty
    FROM dim_dates d LEFT JOIN fact_listening f ON f.date = d.date
    GROUP BY d.month
), previous AS (
    SELECT *, LAG(listening_hours) OVER (ORDER BY month) AS previous_hours
    FROM monthly
)
SELECT *, (listening_hours - previous_hours) / NULLIF(previous_hours, 0) AS hours_change_pct
FROM previous ORDER BY month;
