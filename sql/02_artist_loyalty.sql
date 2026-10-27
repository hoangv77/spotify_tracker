-- Discovery cohort: qualified plays during the first 30 calendar dates after
-- an artist's first observed qualifying play. Dates are local end dates.
SELECT a.artist_name, a.primary_genre, a.first_heard_date,
       SUM(f.qualified_play) AS qualified_plays,
       SUM(f.ms_played) / 3600000.0 AS listening_hours,
       SUM(f.is_returning_artist) * 1.0 / NULLIF(SUM(f.qualified_play), 0) AS artist_loyalty,
       SUM(CASE WHEN f.qualified_play = 1
                     AND julianday(f.date) - julianday(a.first_heard_date) BETWEEN 0 AND 29
                THEN 1 ELSE 0 END) AS plays_first_30_days
FROM fact_listening f JOIN dim_artists a USING (artist_key)
GROUP BY a.artist_key ORDER BY listening_hours DESC;
