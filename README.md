# Spotify Tracker

A local analytics project for Spotify Extended Streaming History: Python/Pandas
data preparation, SQLite queries, statistical analysis, and an editable three-page
Power BI report. Source exports stay outside the repository.

## Quick start

Requires Python 3.10+ and standard Power BI Desktop with Power BI Project/PBIR
support to open the report. Power BI Report Server Desktop is not supported.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[validation]"
.\.venv\Scripts\python.exe -m spotify_tracker build --input "C:\path\to\Spotify Extended Streaming History"
```

Run commands from the repository root. `--timezone America/Chicago` and
`--min-play-seconds 30` are the defaults. The pipeline discovers audio and video
history JSONs, including split files. It never modifies or copies source exports.

## Open the Power BI dashboard

1. Run the pipeline so CSV files exist in `data/processed/`.
2. Open **`powerbi/SpotifyTracker.pbip`** in standard Power BI Desktop.
3. Select **Refresh**. If prompted for file privacy settings, use **Private**
   for the local file source.
4. If the repository moved, set **Transform data > Edit parameters > DataFolder**
   to the absolute `data/processed` folder, then apply and refresh. Running the
   pipeline in the new location also updates this parameter.
5. Use year, artist, and audio/video slicers. Chart selections cross-filter or
   highlight other visuals on the page. Page tabs navigate the report.
6. Save a `.pbix` locally if you want a packaged report. It contains private data
   and is ignored by Git.

There are 37 visuals across three pages:

| Page | Contents |
| --- | --- |
| Your listening, over time | Hours, qualified plays, unique artists/songs, monthly trends, top artists and tracks |
| Discovery & artist loyalty | First-observed songs/artists, discovery rate, artist loyalty, top-five artist share |
| Listening habits & genres | Average minutes by weekday, local hours, known skip rate, primary genres and genre trends |

The project contains PBIP/PBIR report definitions, a CSV-backed semantic model,
relationships, DAX measures, slicers, formatting, and a custom theme. The PNG is
a separate data preview, not a Power BI screenshot. Schema validation does not
execute DAX/M; open and refresh in Desktop for native rendering validation.
See [Power BI setup and model](docs/powerbi.md) for details.

## Genre enrichment

The Spotify export has no genre labels. The dashboard keeps an explicit **Unknown**
category and a **Genre Coverage** card. It does not infer genres from song titles
or silently drop unmapped listening time.

The optional command below sends **artist names only** to MusicBrainz's public
metadata API. It never sends timestamps, play counts, IPs, or account details.
Run it only if you want external enrichment:

```powershell
.\.venv\Scripts\python.exe -m spotify_tracker enrich-genres --top 100
.\.venv\Scripts\python.exe -m spotify_tracker build --input "C:\path\to\Spotify Extended Streaming History" --genres data/metadata/artist_genres.csv
```

Enrichment requires a unique exact name/alias match scoring at least 95, then
selects the MusicBrainz genre with the highest positive vote count. Ties are
resolved alphabetically. Ambiguous entities or missing genre votes stay unmapped.
These are current, community-sourced **artist** genres; they do not establish
each historical song's genre. Review the mapping if a match looks wrong.
Requests are cached and spaced at least 1.1 seconds apart. Existing mappings are
preserved. Artist mappings, lookup audits, and cache files remain local.

For a local workflow, fill `output/artist_genres_to_fill.csv` with one primary
genre and a source per artist, save it as `data/metadata/artist_genres.csv`, and
rerun `build --genres data/metadata/artist_genres.csv`. The example CSV under
`data/metadata/` shows the required columns with placeholder values. One primary
genre per artist avoids double-counting listening time.

## Metric definitions

| Metric | Definition |
| --- | --- |
| Listening duration | Sum of valid music `ms_played`, including short and zero-length events |
| Qualified plays | Music events at least the configured threshold; default 30 seconds |
| Unique artists / songs | Distinct artist keys / track identities among qualified plays |
| Song discovery rate | First-observed qualifying song events in period / qualified plays in period |
| New artists | Artists whose first observed qualified play falls in the period |
| Artist loyalty | Qualified plays to artists first heard on an earlier local date / all qualified plays |
| Top-five artist share | Listening duration to the five leading artists / selected listening duration |
| Known skip rate | Explicit true skips / events with a nonnull explicit skip flag |
| Genre coverage | Duration mapped to a sourced primary artist genre / total music duration |

Discovery is relative to available history, not a release date or proof of first
lifetime exposure. The initial period is left-censored. Artist names are
normalized for stable identity; the export lacks artist IDs, so artists sharing
a name cannot be separated without more metadata. Track URIs identify songs;
title/artist/album is the fallback when a URI is missing.

Daily/hourly buckets use the **playback-end** timestamp in the configured timezone.
Duration is assigned to that end date; plays crossing midnight are not split.
Historical travel timezones are not inferred. Music includes audio and video
events with music metadata. Podcast, audiobook, and unknown-content events are
counted in the audit and excluded from music metrics.

## SQL and statistical analysis

The pipeline writes `data/processed/spotify.sqlite` with a listening fact table,
artist/track/date dimensions, and indexes. Four queries in `sql/` produce monthly
trends, artist loyalty/cohorts, weekday behavior, and genre trends. Results are
saved under `output/sql/`. Monthly SQL results are reconciled against Pandas for
hours, plays, distinct counts, discoveries, and loyalty.

Statistics include a paired weekend-minus-weekday comparison over complete weeks,
a 95% moving-block bootstrap interval, a descriptive monthly slope, lag-one
autocorrelation, and the largest adjacent complete-month change. The bootstrap
resamples four-week blocks, with 4,000 replicates and seed 77. Zero-play dates
are included; missing export coverage is indistinguishable from inactivity.
Associations for one listener are not causal or population conclusions. Partial
boundary months are excluded from monthly slope/change calculations.

`output/threshold_sensitivity.csv` compares metrics at 10, 30, and 60 seconds.

## Project layout and outputs

```text
src/spotify_tracker/        Import, metrics, enrichment, statistics, report generator
sql/                       Four reusable SQLite queries
tests/                     Synthetic regression tests; no private export needed
scripts/                   Power BI schema validator
powerbi/                   Editable PBIP/PBIR, model.bim, theme, and DAX
docs/                      Model and methodology details
data/metadata/              Private genre mapping/cache; ignored
data/processed/             Private CSV tables and SQLite database; ignored
output/                    Private findings, audits, query results, previews; ignored
```

Start with `output/findings.md`, `output/summary.json`, `output/statistics.json`,
and `output/dashboard_preview.png`. The summary includes file hashes, duplicate
counts, invalid-record counts, and content-type counts. Duplicate files are
skipped per media type. Overlapping records are matched using full-record
signatures and occurrence counts; repeated identical events inside a source
file retain their multiplicity.

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/validate_powerbi.py --download-schemas
```

The validator downloads public Microsoft schemas only; no report data is sent.
Schemas are cached in `tmp/` for later offline validation. Tests cover duplicates,
overlap/multiplicity, audio/video separation, qualification, discovery/loyalty,
bad records, timezone/DST, null skips, privacy fields, zero dates/months, genre
mappings, SQL reconciliation, bootstrap behavior, and Power BI field references.

## Privacy and Git

Raw exports may contain IP addresses and sensitive account/activity fields.
These are used transiently for exact duplicate detection and never exported to
the analytical tables. Source history JSONs, generated results, genre lookup
cache/mappings, Power BI caches, and `.pbix` files are ignored by Git. Generic
code and report definitions can be versioned separately. Review staged files
before publishing; Desktop may persist selected data values in report metadata.

The remote is `https://github.com/hoangv77/spotify_tracker.git`. For future work:

```powershell
git switch -c codex/my-feature
git add <source-files>
git diff --cached
git commit -m "Describe the change"
git push -u origin codex/my-feature
```

## References

- [Microsoft: Power BI Projects](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview)
- [Microsoft: report definitions and PBIR](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report)
- [Microsoft: semantic model project files](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-dataset)
- [MusicBrainz: API and genres](https://musicbrainz.org/doc/MusicBrainz_API)
- [MusicBrainz: rate limits](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting)
