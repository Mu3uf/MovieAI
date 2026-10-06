"""Tool 1 - information about ONE specific movie."""
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from services import movie_service, tmdb_service as tmdb


def run(title: str, year: Optional[int] = None) -> dict:
    hit = movie_service.find_movie(title, year)
    if not hit:
        return {"type": "not_found", "movies": [], "message": f"No movie found for '{title}'."}
    details = tmdb.movie_details(hit["id"]) or hit
    card = movie_service.to_card(details, detail=True)
    return {"type": "movie_results", "detail": True, "heading": card["title"], "movies": [card]}


class SearchArgs(BaseModel):
    title: str = Field(description="Movie title as written by the user, in English/original title.")
    year: Optional[int] = Field(default=None, description="Release year, only if the user gave one.")


def make_tool(ctx):
    def _fn(title: str, year: Optional[int] = None) -> str:
        return ctx.call("search_movie", run, title, year)

    return StructuredTool.from_function(
        func=_fn, name="search_movie", args_schema=SearchArgs,
        description="Get facts about ONE specific movie that the user names by title (plot, rating, "
                    "release date). Use for 'tell me about X', 'what is X about'. NEVER use for requests "
                    "with a genre, rating, year, a number of movies, or 'similar to X': use "
                    "recommend_movies for those.")
