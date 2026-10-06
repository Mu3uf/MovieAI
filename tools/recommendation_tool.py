"""Tool 2 - recommendations with structured constraints.

Flow: TMDB candidates (discover / similar / optional vector search)
      -> Python hard filters + quality-aware ranking (Bayesian rating, popularity, genre match)
      -> only the best N movies are returned. The LLM never sees the candidate pool."""
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from services import movie_service as ms, tmdb_service as tmdb


def run(ctx, genre=None, exclude_genre=None, actor=None, director=None, start_year=None,
        end_year=None, min_rating=None, language=None, number_of_movies=5, similar_to=None,
        mood_query=None, exclude_previous=False, use_user_preferences=True) -> dict:
    n = max(1, min(int(number_of_movies or 5), 10))
    notes: list[str] = []

    include_ids, mood_words = ms.parse_genres(genre)
    exclude_gids, _ = ms.parse_genres(exclude_genre)
    exclude_gids = set(exclude_gids)
    exclude_gids |= set(ms.exclusions_from_text(getattr(ctx, "message", ""))) - set(include_ids)
    if genre and not include_ids and not mood_words:
        mood_words = [genre]

    banned = set(ctx.seen_ids) if exclude_previous else set()
    preferred_ids: list[int] = []
    any_ids: list[int] = []
    explicit = bool(include_ids or similar_to or actor or director)

    # ---- stored preferences: used only as soft help; the explicit request always wins
    if use_user_preferences and ctx.prefs:
        pref_ids, _ = ms.parse_genres(", ".join(ctx.prefs.get("preferred_genres") or []))
        dis_ids, _ = ms.parse_genres(", ".join(ctx.prefs.get("disliked_genres") or []))
        exclude_gids |= set(dis_ids) - set(include_ids)
        banned |= set(ctx.interacted_ids)
        preferred_ids = pref_ids
        if not explicit:
            any_ids = pref_ids
            tags = [t.lower() for t in ctx.prefs.get("preferred_genres") or []
                    if t.lower() not in tmdb.GENRE_NAME_TO_ID and t.lower() not in ms.GENRE_ALIASES]
            mood_words = list(dict.fromkeys(mood_words + tags))
        if ctx.prefs.get("preferred_min_rating") and min_rating is None:
            min_rating = float(ctx.prefs["preferred_min_rating"])

    lang = ms.parse_language(language) or None
    if language and not lang:
        notes.append(f"Language '{language}' was not recognised and was ignored.")

    cast_id = crew_id = None
    if actor:
        cast_id = tmdb.search_person(actor)
        if not cast_id:
            return {"type": "no_results", "movies": [], "message": f"I couldn't find an actor named '{actor}'."}
    if director:
        crew_id = tmdb.search_person(director)
        if not crew_id:
            return {"type": "no_results", "movies": [], "message": f"I couldn't find a director named '{director}'."}
        notes.append("Director filter matches anyone credited in the crew with that name.")
    strict = not (actor or director or lang or start_year or end_year or similar_to or mood_query or mood_words)
    min_votes = 3000 if strict else 200
    # ---- candidates
    cands: list[dict] = []
    heading = "Recommended for you"
    if similar_to:
        base = ms.find_movie(similar_to)
        if not base:
            return {"type": "not_found", "movies": [], "message": f"No movie found for '{similar_to}'."}
        banned.add(base["id"])
        cands = ms.similar_candidates(base["id"])
        heading = f"Because you like {base.get('title')}"
    else:
        semantic_q = " ".join(filter(None, [mood_query, " ".join(mood_words)])).strip()
        if semantic_q:
            cands = ms.semantic_candidates(semantic_q)
            if not cands:
                notes.append("Mood/theme search is not enabled, so only structured filters were used.")
        if len(cands) < n * 3:
            cands += ms.discover_candidates(
                include_ids=include_ids, any_ids=any_ids, exclude_ids=exclude_gids,
                start_year=start_year, end_year=end_year, min_rating=min_rating, language=lang,
                cast_id=cast_id, crew_id=crew_id, extra_pages=min(3, len(banned) // 5),
                vote_floor=5000 if strict else 300)

    ranked = ms.rank_and_filter(
        cands, n=n, min_rating=min_rating, start_year=start_year, end_year=end_year,
        include_genre_ids=include_ids, exclude_genre_ids=exclude_gids, exclude_ids=banned,
        preferred_genre_ids=preferred_ids, min_votes=min_votes)

    movies = [ms.to_card(m) for m in ranked]
    applied = {k: v for k, v in dict(genre=genre, exclude_genre=exclude_genre, actor=actor,
               director=director, start_year=start_year, end_year=end_year, min_rating=min_rating,
               language=lang, similar_to=similar_to, n=n).items() if v}
    if not movies:
        return {"type": "no_results", "movies": [], "applied": applied,
                "message": "No movies satisfy all of those constraints. Try relaxing one of them.",
                "note": " ".join(notes)}
    if len(movies) < n:
        notes.append(f"Only {len(movies)} movies met every constraint, so fewer than {n} are shown.")
    return {"type": "movie_results", "heading": heading, "movies": movies, "applied": applied,
            "note": " ".join(notes)}


class RecommendArgs(BaseModel):
    genre: Optional[str] = Field(default=None, description="Genre(s) wanted, English, e.g. 'action' or 'sci-fi thriller'. Leave empty to use the user's stored taste.")
    exclude_genre: Optional[str] = Field(default=None, description="Genre(s) to exclude, e.g. 'animation'.")
    actor: Optional[str] = Field(default=None, description="Actor name.")
    director: Optional[str] = Field(default=None, description="Director name.")
    start_year: Optional[int] = Field(default=None, description="Earliest release year.")
    end_year: Optional[int] = Field(default=None, description="Latest release year.")
    min_rating: Optional[float] = Field(default=None, description="Minimum TMDB rating 0-10.")
    language: Optional[str] = Field(default=None, description="Original language, e.g. 'korean' or 'ko'.")
    number_of_movies: int = Field(default=5, description="How many movies (1-10).")
    similar_to: Optional[str] = Field(default=None, description="Title of a movie the user wants similar ones to.")
    mood_query: Optional[str] = Field(default=None, description="Mood/theme words if not a plain genre, e.g. 'dark, character questions reality'.")
    exclude_previous: bool = Field(default=False, description="True when the user wants different/other/new ones than already shown, or dislikes the shown ones.")
    use_user_preferences: bool = Field(default=True, description="Use the stored profile (disliked genres, watched movies). Explicit request values always win.")


def make_tool(ctx):
    def _fn(**kwargs) -> str:
        return ctx.call("recommend_movies", run, ctx, **kwargs)

    return StructuredTool.from_function(
        func=_fn, name="recommend_movies", args_schema=RecommendArgs,
        description="Recommend or list movies by genre, year range, rating, language, actor, director, "
                    "mood, or 'similar to X'. Also for 'recommend something for me' (stored taste is applied). "
                    "Convert every constraint the user states into arguments.")
