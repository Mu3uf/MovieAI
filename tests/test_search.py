import pytest
from config import settings
from tools import search_movie_tool

pytestmark = pytest.mark.live
needs_tmdb = pytest.mark.skipif(not settings.TMDB_API_KEY, reason="TMDB_API_KEY not set")


@needs_tmdb
def test_search_interstellar():
    r = search_movie_tool.run("Interstellar")
    assert r["type"] == "movie_results"
    m = r["movies"][0]
    assert m["title"] == "Interstellar" and m["id"] == 157336 and m["year"] == 2014


@needs_tmdb
def test_search_not_found():
    r = search_movie_tool.run("zzzqqqxxyy not a real movie 98765")
    assert r["type"] == "not_found" and r["movies"] == []
