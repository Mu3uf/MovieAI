import pytest
from config import settings
from tools import trending_tool


@pytest.mark.live
@pytest.mark.skipif(not settings.TMDB_API_KEY, reason="TMDB_API_KEY not set")
def test_trending_returns_movies():
    r = trending_tool.run(10)
    assert r["type"] == "movie_results" and 0 < len(r["movies"]) <= 10
    assert all(m["id"] and m["title"] for m in r["movies"])
