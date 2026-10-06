"""Tool 4 - all movies of a franchise (TMDB collection), in release order."""
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from services import movie_service, tmdb_service as tmdb


def run(title: str, year: Optional[int] = None) -> dict:
    hit = movie_service.find_movie(title, year)
    if not hit:
        return {"type": "not_found", "movies": [], "message": f"No movie found for '{title}'."}
    details = tmdb.movie_details(hit["id"]) or {}
    col = details.get("belongs_to_collection")
    if not col:
        return {"type": "no_collection", "movies": [],
                "message": f"'{hit.get('title')}' does not belong to a TMDB collection (no known series)."}
    data = tmdb.collection(col["id"])
    if not data or not data.get("parts"):
        return {"type": "no_collection", "movies": [], "message": "The collection could not be found."}
    parts = sorted(data["parts"], key=lambda m: (not m.get("release_date"), m.get("release_date") or ""))
    return {"type": "movie_results", "kind": "collection", "collection": data.get("name"),
            "heading": data.get("name"), "movies": [movie_service.to_card(m) for m in parts]}


class CollectionArgs(BaseModel):
    title: str = Field(description="Title of any movie in the series, e.g. 'The Godfather'.")
    year: Optional[int] = Field(default=None, description="Release year if the title is ambiguous.")


def make_tool(ctx):
    def _fn(title: str, year: Optional[int] = None) -> str:
        return ctx.call("movie_collection", run, title, year)

    return StructuredTool.from_function(
        func=_fn, name="movie_collection", args_schema=CollectionArgs,
        description="List ALL movies of a series/franchise (sequels, prequels) in release order. "
                    "Use for 'all the X movies', 'X series', 'in order'.")
