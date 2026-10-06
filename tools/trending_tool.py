"""Tool 3 - trending movies, read from the Supabase cache (TMDB only as a fallback)."""
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from services import movie_service


def run(limit: int = 10) -> dict:
    movies = movie_service.get_trending(limit=max(1, min(limit, 20)))
    if not movies:
        return {"type": "no_results", "movies": [], "message": "No trending data is available right now."}
    return {"type": "movie_results", "heading": "Trending now", "movies": movies}


class TrendingArgs(BaseModel):
    pass


def make_tool(ctx):
    def _fn() -> str:
        return ctx.call("trending_movies", run)

    return StructuredTool.from_function(
        func=_fn, name="trending_movies", args_schema=TrendingArgs,
        description="Movies that are trending / popular right now. Takes no arguments.")
