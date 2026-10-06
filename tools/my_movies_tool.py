"""Tool 5 - the user's OWN movies from user_movie_interactions (liked / watched / saved / disliked).
Returns ALL of them (no top-5 cut-off) unless a limit is asked for. RLS guarantees only this user's rows."""
from typing import Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from services import movie_service

KINDS = ("liked", "watched", "saved", "disliked")


def run(ctx, interaction_type: str = "liked", sort_by: str = "recent", limit: Optional[int] = None) -> dict:
    kinds = [k for k in (t.strip().lower() for t in str(interaction_type or "").replace(" and ", ",").split(","))
             if k in KINDS] or ["liked"]
    label = " / ".join(kinds)
    if ctx.db is None:
        return {"type": "error", "movies": [], "message": "Your activity is not available right now."}

    rows = (ctx.db.from_("user_movie_interactions")
            .select("tmdb_id,interaction_type,title,poster_path,created_at")
            .in_("interaction_type", kinds).order("created_at", desc=True).limit(500).execute().data)
    if not rows:
        return {"type": "no_results", "movies": [],
                "message": f"You haven't marked any movies as {label} yet."}

    order, first_row = [], {}
    for r in rows:  # a movie that is both liked and watched appears once
        if r["tmdb_id"] not in first_row:
            order.append(r["tmdb_id"])
            first_row[r["tmdb_id"]] = r
    cards = movie_service.cards_for_ids(order, first_row)
    movies = [cards[i] for i in order]  # default: most recently marked first

    if sort_by == "rating":
        movies.sort(key=lambda m: (m["rating"], m["vote_count"]), reverse=True)
    elif sort_by == "release_date":
        movies.sort(key=lambda m: m["release_date"] or "", reverse=True)

    total = len(movies)
    if limit:
        movies = movies[: max(1, min(int(limit), 500))]
    heading = f"Your {label} movies"
    if sort_by == "rating" and limit:
        heading = f"Your top-rated {label} movies"
    return {"type": "movie_results", "heading": heading, "movies": movies,
            "note": f"The user has {total} {label} movie(s); {len(movies)} are shown on screen."}


class MyMoviesArgs(BaseModel):
    interaction_type: str = Field(default="liked", description="'liked', 'watched', 'saved' or 'disliked'. Several may be comma-separated, e.g. 'liked,watched'.")
    sort_by: Literal["recent", "rating", "release_date"] = Field(default="recent", description="'rating' for highest rated first, 'release_date' for newest first, 'recent' for most recently marked.")
    limit: Optional[int] = Field(default=None, description="How many to show. Leave empty to show ALL. Use 1 for 'the highest rated one', or the number the user asked for.")


def make_tool(ctx):
    def _fn(interaction_type: str = "liked", sort_by: str = "recent", limit: Optional[int] = None) -> str:
        return ctx.call("my_movies", run, ctx, interaction_type, sort_by, limit)

    return StructuredTool.from_function(
        func=_fn, name="my_movies", args_schema=MyMoviesArgs,
        description="Show the user's OWN saved activity: movies they liked, watched, saved or disliked. "
                    "Use for 'show my liked movies', 'what did I watch', 'highest rated movie I watched'. "
                    "NOT for new recommendations.")