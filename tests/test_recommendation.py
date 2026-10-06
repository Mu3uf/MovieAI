"""Offline tests for ranking/filter/parsing (no keys needed) + one live constraint test."""
import pytest
from config import settings
from services import movie_service as ms
from tools import ToolContext, recommendation_tool


def mv(i, rating, votes, year, genres, pop=50):
    return {"id": i, "title": f"M{i}", "vote_average": rating, "vote_count": votes, "popularity": pop,
            "release_date": f"{year}-06-01", "genre_ids": genres}


def test_parse_genres():
    assert ms.parse_genres("sci-fi thriller")[0] == [878, 53] or set(ms.parse_genres("sci-fi thriller")[0]) == {878, 53}
    ids, left = ms.parse_genres("psychological thriller")
    assert ids == [53] and "psychological" in left
    assert ms.parse_genres("animated")[0] == [16]


def test_hard_constraints_never_violated():
    c = [mv(1, 8.5, 5000, 2012, [28]), mv(2, 8.2, 4000, 2015, [28, 16]), mv(3, 7.0, 9000, 2014, [28]),
         mv(4, 9.0, 12, 2013, [28]), mv(5, 8.4, 3000, 2005, [28]), mv(6, 8.1, 3000, 2019, [28, 12])]
    out = ms.rank_and_filter(c, n=5, min_rating=8, start_year=2010, end_year=2020,
                             include_genre_ids=[28], exclude_genre_ids=[16])
    assert {m["id"] for m in out} == {1, 6}   # 2 animation, 3 low rating, 4 few votes, 5 too old


def test_fewer_instead_of_violating():
    assert ms.rank_and_filter([mv(1, 7.0, 5000, 2012, [28])], n=5, min_rating=8) == []


def test_exclusions_and_dedup():
    c = [mv(1, 8, 999, 2012, [28]), mv(1, 8, 999, 2012, [28]), mv(2, 8, 999, 2012, [28])]
    assert [m["id"] for m in ms.rank_and_filter(c, n=5, exclude_ids={2})] == [1]


def test_bayesian_prefers_many_votes():
    assert ms.bayesian(8.0, 20000) > ms.bayesian(8.6, 60)


@pytest.mark.live
@pytest.mark.skipif(not settings.TMDB_API_KEY, reason="TMDB_API_KEY not set")
def test_live_action_above_8():
    r = recommendation_tool.run(ToolContext(), genre="action", min_rating=8, number_of_movies=5)
    assert all(m["rating"] >= 8 and "Action" in m["genres"] for m in r["movies"])
