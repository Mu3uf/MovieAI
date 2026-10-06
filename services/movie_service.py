"""Movie logic: normalising TMDB data, genre parsing, candidate fetching, ranking.

Rule: Python filters and ranks. The LLM only ever sees a handful of final movies."""
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from config import settings
from services import tmdb_service as tmdb

log = logging.getLogger("movies")

# ----------------------------------------------------------------- genres / languages
GENRE_ALIASES = {
    "sci-fi": 878, "scifi": 878, "sci fi": 878, "science-fiction": 878,
    "animated": 16, "cartoon": 16, "cartoons": 16, "anime": 16,
    "romantic": 10749, "rom-com": 10749, "romcom": 10749,
    "scary": 27, "suspense": 53, "suspenseful": 53, "docs": 99,
    "kids": 10751, "superhero": 28,
}
_GENRE_TABLE = {**tmdb.GENRE_NAME_TO_ID, **GENRE_ALIASES}
_STOP = {"and", "the", "movies", "movie", "films", "film", "with", "for", "genre", "some",
         "very", "that", "are", "any", "kind", "type", "good", "great", "best", "new", "old"}

LANGUAGES = {
    "english": "en", "arabic": "ar", "french": "fr", "spanish": "es", "german": "de",
    "italian": "it", "japanese": "ja", "korean": "ko", "chinese": "zh", "mandarin": "zh",
    "hindi": "hi", "turkish": "tr", "russian": "ru", "portuguese": "pt", "persian": "fa",
    "swedish": "sv", "danish": "da", "thai": "th",
}


def _pattern(key: str) -> str:
    """Genre word with optional plural: thriller(s), comed(y|ies), drama(s)."""
    body = re.escape(key[:-1]) + r"(?:y|ies)" if key.endswith("y") else re.escape(key) + r"s?"
    return rf"(?<![a-z]){body}(?![a-z])"


def parse_genres(text: str | None) -> tuple[list[int], list[str]]:
    """'psychological thriller, sci-fi' -> ([53, 878], ['psychological'])."""
    if not text:
        return [], []
    low = text.lower()
    ids: list[int] = []
    for key in sorted(_GENRE_TABLE, key=len, reverse=True):
        pattern = _pattern(key)
        if re.search(pattern, low):
            ids.append(_GENRE_TABLE[key])
            low = re.sub(pattern, " ", low)
    leftover = [w for w in re.findall(r"[a-z][a-z\-]+", low) if w not in _STOP and len(w) > 2]
    return list(dict.fromkeys(ids)), leftover
_EXCL = re.compile(
    r"\b(?:not|no|without|except|excluding|exclude|avoid|skip)\b\s+"
    r"([^.,;!?]+?)(?=\s+(?:but|and|with|from|rated|that|which|in|for|please|between|after|before)\b|[.,;!?]|$)",
    re.I)


def exclusions_from_text(text: str | None) -> list[int]:
    """Genres the user ruled out in plain words: 'not anime or animation', 'no horror', 'without comedy'.
    Safety net so an exclusion is applied even if the LLM forgets to pass exclude_genre."""
    ids: list[int] = []
    for frag in _EXCL.findall(text or ""):
        ids += parse_genres(frag)[0]
    return list(dict.fromkeys(ids))

def parse_language(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    if v in LANGUAGES:
        return LANGUAGES[v]
    return v if re.fullmatch(r"[a-z]{2}", v) else None


# ----------------------------------------------------------------- normalising
def _year(date: str | None) -> int | None:
    try:
        return int((date or "")[:4])
    except ValueError:
        return None


def genre_names(m: dict) -> list[str]:
    g = m.get("genres")
    if g:
        return [x["name"] if isinstance(x, dict) else str(x) for x in g]
    return [tmdb.GENRES[i] for i in (m.get("genre_ids") or []) if i in tmdb.GENRES]


def to_card(m: dict, detail: bool = False) -> dict:
    """Uniform movie dict sent to the frontend (and a trimmed version to the LLM)."""
    poster = m.get("poster_path")
    card = {
        "id": m.get("id"),
        "title": m.get("title") or m.get("original_title") or "Untitled",
        "rating": round(float(m.get("vote_average") or 0), 1),
        "vote_count": m.get("vote_count") or 0,
        "release_date": m.get("release_date") or "",
        "year": _year(m.get("release_date")),
        "poster_path": poster,
        "poster_url": f"{settings.TMDB_IMAGE_BASE}/w342{poster}" if poster else None,
        "overview": (m.get("overview") or "")[:420],
        "genres": genre_names(m),
    }
    if detail:
        card["tagline"] = m.get("tagline") or ""
        card["runtime"] = m.get("runtime")
    return card


def _db_to_raw(row: dict) -> dict:
    """Rows from our own tables -> same shape as TMDB results."""
    names = row.get("genres") or []
    return {
        "id": row.get("tmdb_id"), "title": row.get("title"), "overview": row.get("overview"),
        "release_date": row.get("release_date"), "vote_average": row.get("rating"),
        "vote_count": row.get("vote_count"), "popularity": row.get("popularity"),
        "poster_path": row.get("poster_path"), "genres": names,
        "genre_ids": [tmdb.GENRE_NAME_TO_ID[n.lower()] for n in names if n.lower() in tmdb.GENRE_NAME_TO_ID],
    }


# ----------------------------------------------------------------- ranking
def bayesian(rating: float, votes: int, prior_mean: float = 6.5, prior_votes: int = 500) -> float:
    """IMDb-style weighted rating: pulls movies with few votes toward the global mean."""
    v = votes or 0
    return (v / (v + prior_votes)) * rating + (prior_votes / (v + prior_votes)) * prior_mean


def score(m: dict, preferred_ids=()) -> float:
    wr = bayesian(float(m.get("vote_average") or 0), m.get("vote_count") or 0)
    pop = math.log10(1 + float(m.get("popularity") or 0))
    pref = len(set(m.get("genre_ids") or []) & set(preferred_ids))
    return wr + 0.25 * pop + 0.4 * pref + float(m.get("_rel") or 0)


def rank_and_filter(cands, *, n=5, min_rating=None, start_year=None, end_year=None,
                    include_genre_ids=(), exclude_genre_ids=(), exclude_ids=(),
                    preferred_genre_ids=(), min_votes=100) -> list[dict]:
    """Hard constraints are never violated: fewer results are returned instead."""
    seen, kept = set(), []
    include, exclude, banned = set(include_genre_ids), set(exclude_genre_ids), set(exclude_ids)
    for m in cands:
        mid = m.get("id")
        if mid is None or mid in seen or mid in banned:
            continue
        seen.add(mid)
        if (m.get("vote_count") or 0) < min_votes:
            continue
        if min_rating is not None and float(m.get("vote_average") or 0) < min_rating:
            continue
        year = _year(m.get("release_date"))
        if start_year is not None and (year is None or year < start_year):
            continue
        if end_year is not None and (year is None or year > end_year):
            continue
        gids = set(m.get("genre_ids") or [])
        if gids & exclude:
            continue
        if include and not include <= gids:
            continue
        kept.append((score(m, preferred_genre_ids), m))
    kept.sort(key=lambda t: t[0], reverse=True)
    return [m for _, m in kept[:n]]


# ----------------------------------------------------------------- candidate sources
def find_movie(title: str, year: int | None = None) -> dict | None:
    """Best match for a title: exact-title match with most votes, else TMDB's top hit."""
    results = tmdb.search_movie(title, year)
    if not results:
        return None
    q = title.strip().lower()
    exact = [r for r in results if (r.get("title") or "").lower() == q
             or (r.get("original_title") or "").lower() == q]
    if exact:
        return max(exact, key=lambda r: r.get("vote_count") or 0)
    return results[0]


def discover_candidates(*, include_ids=(), any_ids=(), exclude_ids=(), start_year=None,
                        end_year=None, min_rating=None, language=None, cast_id=None,
                        crew_id=None, extra_pages=0, vote_floor=400) -> list[dict]:
    base = {
        "include_adult": "false", "language": "en-US",
        "with_original_language": language,
        "primary_release_date.gte": f"{start_year}-01-01" if start_year else None,
        "primary_release_date.lte": f"{end_year}-12-31" if end_year else None,
        "vote_average.gte": min_rating,
        "with_cast": cast_id, "with_crew": crew_id,
    }
    if include_ids:
        base["with_genres"] = ",".join(map(str, include_ids))      # AND
    elif any_ids:
        base["with_genres"] = "|".join(map(str, list(any_ids)[:3]))  # OR
    if exclude_ids:
        base["without_genres"] = ",".join(map(str, exclude_ids))
    jobs = []
    for page in range(1, 2 + extra_pages):
        jobs.append({**base, "sort_by": "vote_average.desc", "vote_count.gte": vote_floor, "page": page})
        jobs.append({**base, "sort_by": "popularity.desc", "vote_count.gte": vote_floor, "page": page})

    def fetch(p):
        try:
            return tmdb.discover(p)
        except tmdb.TMDBError as e:
            return e

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(fetch, jobs))
    ok = [o for o in outcomes if isinstance(o, list)]
    if not ok:
        raise next(o for o in outcomes if isinstance(o, Exception))
    return [m for batch in ok for m in batch]


def similar_candidates(movie_id: int) -> list[dict]:
    """TMDB's own 'recommendations' + 'similar' lists; earlier = more relevant."""
    out = []
    for fn in (tmdb.recommendations, tmdb.similar):
        try:
            batch = fn(movie_id)
        except tmdb.TMDBError:
            batch = []
        for i, m in enumerate(batch):
            m["_rel"] = 1.0 * (1 - i / max(len(batch), 1))
        out += batch
    return out


# ----------------------------------------------------------------- optional vector search
_embedder = None


def embed_texts(texts: list[str]) -> list[list[float]]:
    global _embedder
    from fastembed import TextEmbedding  # optional dependency (free, local, CPU)

    if _embedder is None:
        _embedder = TextEmbedding("BAAI/bge-small-en-v1.5")  # 384 dims, loaded once
    return [v.tolist() for v in _embedder.embed(texts)]


def semantic_candidates(query: str, limit: int = 40) -> list[dict]:
    """Mood/theme search. Returns [] when disabled or on any error (caller falls back)."""
    if not settings.EMBEDDINGS_ENABLED or not query.strip():
        return []
    try:
        from database.supabase_db import public_client

        vec = embed_texts([query])[0]
        rows = public_client().rpc("match_movies", {"query_embedding": vec, "match_count": limit}).execute().data
        out = []
        for r in rows or []:
            raw = _db_to_raw(r)
            raw["_rel"] = float(r.get("similarity") or 0) * 6
            out.append(raw)
        return out
    except Exception:
        log.exception("semantic search failed; falling back to structured filters")
        return []


# ----------------------------------------------------------------- cached lists
def _fresh(ts: str | None) -> bool:
    try:
        dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - dt < timedelta(hours=settings.CACHE_MAX_AGE_HOURS)
    except ValueError:
        return True


def _cached(table: str, order: str, live_fn, limit: int, desc: bool = False) -> list[dict]:
    """Read the Supabase cache; only call TMDB if the cache is empty/stale/unreachable."""
    try:
        from database.supabase_db import public_client

        rows = public_client().from_(table).select("*").order(order, desc=desc).limit(limit).execute().data
        if rows and _fresh(rows[0].get("fetched_at")):
            return [to_card(_db_to_raw(r)) for r in rows]
    except Exception:
        log.exception("cache read failed for %s; using live TMDB", table)
    return [to_card(m) for m in live_fn()[:limit]]


def get_trending(limit: int = 20) -> list[dict]:
    return _cached("trending_movies", "position", lambda: tmdb.trending("week"), limit)


def get_new(limit: int = 20) -> list[dict]:
    return _cached("new_movies", "release_date", lambda: tmdb.now_playing(), limit, desc=True)


# ----------------------------------------------------------------- tmdb ids -> cards (for the user's own movies)
def cards_for_ids(ids: list[int], fallback: dict | None = None) -> dict[int, dict]:
    """Our Supabase 'movies' table first (one query), TMDB for the rest (parallel), stored title as last resort."""
    fallback, cards = fallback or {}, {}
    if ids:
        try:
            from database.supabase_db import public_client

            rows = (public_client().from_("movies")
                    .select("tmdb_id,title,overview,release_date,rating,vote_count,popularity,poster_path,genres")
                    .in_("tmdb_id", ids).execute().data)
            for r in rows or []:
                cards[r["tmdb_id"]] = to_card(_db_to_raw(r))
        except Exception:
            log.exception("could not read movies table; falling back to TMDB")
    missing = [i for i in ids if i not in cards]

    def fetch(i):
        try:
            d = tmdb.movie_details(i)
            return to_card(d) if d else None
        except tmdb.TMDBError:
            return None

    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for i, c in zip(missing, pool.map(fetch, missing)):
                if c:
                    cards[i] = c
    for i in ids:
        if i not in cards:
            fb = fallback.get(i) or {}
            cards[i] = to_card({"id": i, "title": fb.get("title") or f"Movie {i}", "poster_path": fb.get("poster_path")})
    return cards
