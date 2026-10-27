-- A single primary genre per artist prevents double-counting listening time.
SELECT d.month, a.primary_genre,
       SUM(f.ms_played) / 3600000.0 AS listening_hours,
       SUM(f.qualified_play) AS qualified_plays
FROM fact_listening f
JOIN dim_artists a USING (artist_key)
JOIN dim_dates d ON d.date = f.date
GROUP BY d.month, a.primary_genre ORDER BY d.month, listening_hours DESC;
