import pytest
from config import settings
from tools import collection_tool

live = pytest.mark.skipif(not settings.TMDB_API_KEY, reason="TMDB_API_KEY not set")


@pytest.mark.live
@live
def test_godfather_collection_in_release_order():
    r = collection_tool.run("The Godfather")
    dates = [m["release_date"] for m in r["movies"]]
    assert len(dates) >= 3 and dates == sorted(dates)


@pytest.mark.live
@live
def test_standalone_movie_has_no_collection():
    r = collection_tool.run("Se7en")
    assert r["type"] in ("no_collection", "movie_results")
