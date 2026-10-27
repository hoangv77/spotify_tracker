"""Optional MusicBrainz enrichment. Sends artist names only, with local caching."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd


USER_AGENT = "SpotifyTracker/0.1 (https://github.com/hoangv77/spotify_tracker)"


def normalized_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold().strip()


def exact_artist_match(artists: list[dict], name: str) -> dict | None:
    target = normalized_name(name)
    matches = {}
    for artist in artists:
        names = [artist.get("name", ""), *[alias.get("name", "") for alias in artist.get("aliases", [])]]
        if int(artist.get("score", 0)) >= 95 and target in [normalized_name(n) for n in names]:
            matches[artist["id"]] = artist
    return next(iter(matches.values())) if len(matches) == 1 else None


def select_genre(genres: list[dict]) -> str | None:
    candidates = [g for g in genres if g.get("name") and int(g.get("count", 0)) > 0]
    if not candidates:
        return None
    return sorted(candidates, key=lambda g: (-int(g["count"]), g["name"]))[0]["name"]


class MusicBrainzClient:
    def __init__(self, cache: Path):
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)
        self.last_request = 0.0

    def get(self, suffix: str) -> dict:
        path = self.cache / (hashlib.sha256(suffix.encode()).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        for attempt in range(3):
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
            request = urllib.request.Request("https://musicbrainz.org/ws/2/" + suffix, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                self.last_request = time.monotonic()
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = json.load(response)
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            except urllib.error.HTTPError as error:
                if error.code not in [429, 500, 502, 503, 504] or attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError("MusicBrainz request failed")


def enrich_genres(project: Path, top: int) -> Path:
    if top < 1:
        raise ValueError("--top must be positive")
    ranking = pd.read_csv(project / "data" / "processed" / "top_artists.csv")
    metadata = project / "data" / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    target = metadata / "artist_genres.csv"
    # Preserve manual mappings and earlier successful enrichment.
    existing = pd.read_csv(target, dtype=str).fillna("") if target.exists() else pd.DataFrame(columns=["artist_name", "primary_genre", "source"])
    mappings = existing.to_dict("records")
    known = {normalized_name(row["artist_name"]) for row in mappings}
    client = MusicBrainzClient(metadata / "cache")
    audit = []
    for i, name in enumerate(ranking["artist_name"].head(top), 1):
        if normalized_name(name) in known:
            continue
        try:
            escaped = name.replace("\\", "\\\\").replace('"', '\\"')
            query = urllib.parse.urlencode({"query": f'artist:"{escaped}"', "fmt": "json", "limit": 25})
            search = client.get("artist/?" + query)
            match = exact_artist_match(search.get("artists", []), name)
            if match is None:
                status = "ambiguous_or_unmatched"
            else:
                data = client.get(f"artist/{match['id']}?inc=genres&fmt=json")
                genre = select_genre(data.get("genres", []))
                if genre:
                    mappings.append({"artist_name": name, "primary_genre": genre, "source": f"https://musicbrainz.org/artist/{match['id']}"})
                    known.add(normalized_name(name))
                    status = "mapped"
                else:
                    status = "no_positive_genre_votes"
            audit.append({"artist_name": name, "status": status})
        except (OSError, ValueError) as error:
            audit.append({"artist_name": name, "status": "request_failed", "error": str(error)})
        # Save incremental results so interrupted enrichment can resume safely.
        pd.DataFrame(mappings, columns=["artist_name", "primary_genre", "source"]).to_csv(target, index=False)
        if i % 10 == 0 or i == min(top, len(ranking)):
            print(f"Genre lookup: {i}/{min(top, len(ranking))} artists checked; {len(mappings)} sourced mappings saved.", flush=True)
    (project / "output" / "genre_lookup_audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Genre mapping: {target}", flush=True)
    return target
