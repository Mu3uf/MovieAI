"""TMDB client. One persistent HTTP client (connection reuse), small helpers, friendly errors."""
import logging

import httpx

from config import settings

log = logging.getLogger("tmdb")

# TMDB genre ids are stable, so we ship them (saves a network call on every cold start).
GENRES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance",
    878: "Science Fiction", 10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
}
GENRE_NAME_TO_ID = {v.lower(): k for k, v in GENRES.items()}


class TMDBError(Exception):
    """Raised with a message that is safe to show to the user."""


_client = httpx.Client(
    base_url="https://api.themoviedb.org/3",
    timeout=httpx.Timeout(10.0, connect=5.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)


def _get(path: str, **params):
    headers = {}
    key = settings.TMDB_API_KEY
    if not key:
        raise TMDBError("TMDB API key is not configured.")
    if key.startswith("eyJ"):  # v4 read-access token
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    try:
        r = _client.get(path, params=params, headers=headers)
    except httpx.HTTPError as e:
        log.error("TMDB network error on %s: %s", path, type(e).__name__)
        raise TMDBError("The movie database is not reachable right now.") from e
    if r.status_code == 401:
        raise TMDBError("The TMDB API key is invalid.")
    if r.status_code == 404:
        return None
    if r.status_code == 429:
        raise TMDBError("The movie database is rate-limiting us. Try again in a moment.")
    if r.status_code >= 400:
        log.error("TMDB %s on %s", r.status_code, path)
        raise TMDBError("The movie database returned an error.")
    return r.json()


def search_movie(query: str, year: int | None = None) -> list[dict]:
    data = _get("/search/movie", query=query, year=year, include_adult="false", language="en-US")
    return (data or {}).get("results", [])


def movie_details(movie_id: int) -> dict | None:
    return _get(f"/movie/{movie_id}", language="en-US")


def search_person(name: str) -> int | None:
    data = _get("/search/person", query=name, include_adult="false") or {}
    results = data.get("results", [])
    return results[0]["id"] if results else None


def collection(collection_id: int) -> dict | None:
    return _get(f"/collection/{collection_id}", language="en-US")


def discover(params: dict) -> list[dict]:
    return (_get("/discover/movie", **params) or {}).get("results", [])


def trending(window: str = "week") -> list[dict]:
    return (_get(f"/trending/movie/{window}", language="en-US") or {}).get("results", [])


def now_playing(page: int = 1) -> list[dict]:
    return (_get("/movie/now_playing", page=page, language="en-US") or {}).get("results", [])


def popular(page: int = 1) -> list[dict]:
    return (_get("/movie/popular", page=page, language="en-US") or {}).get("results", [])


def top_rated(page: int = 1) -> list[dict]:
    return (_get("/movie/top_rated", page=page, language="en-US") or {}).get("results", [])


def recommendations(movie_id: int, page: int = 1) -> list[dict]:
    return (_get(f"/movie/{movie_id}/recommendations", page=page) or {}).get("results", [])


def similar(movie_id: int, page: int = 1) -> list[dict]:
    return (_get(f"/movie/{movie_id}/similar", page=page) or {}).get("results", [])
