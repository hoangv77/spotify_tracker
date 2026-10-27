-- Average per calendar day, including zero-listening days.
WITH daily AS (
    SELECT d.date, d.weekday_number, d.weekday, d.is_weekend,
           COALESCE(SUM(f.ms_played) / 60000.0, 0) AS listening_minutes,
           COALESCE(SUM(f.qualified_play), 0) AS qualified_plays
    FROM dim_dates d LEFT JOIN fact_listening f ON f.date = d.date
    GROUP BY d.date
)
SELECT weekday_number, weekday, COUNT(*) AS calendar_days,
       AVG(listening_minutes) AS mean_minutes_per_day,
       AVG(qualified_plays) AS mean_qualified_plays_per_day
FROM daily GROUP BY weekday_number, weekday ORDER BY weekday_number;
