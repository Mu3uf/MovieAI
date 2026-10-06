"""Memory extraction (offline) + live agent tool-selection tests (need LLM + TMDB + Supabase keys)."""
import pytest
from config import settings
from services import memory_service as mem


def test_preference_extraction_spec_example():
    assert "Thriller" in mem.extract_preferences("I really like psychological thrillers.")["like_genres"]
    assert "Psychological" in mem.extract_preferences("I really like psychological thrillers.")["like_genres"]
    assert mem.extract_preferences("I hate animation.")["dislike_genres"] == ["Animation"]
    assert mem.extract_preferences("I loved The Dark Knight.")["like_movies"] == ["The Dark Knight"]
    assert "Horror" in mem.extract_preferences("I hate horror movies")["dislike_genres"]
    assert "Animation" in mem.extract_preferences("I don't like animated movies.")["dislike_genres"]


def test_requests_are_not_preferences():
    ex = mem.extract_preferences("Give me 5 action movies like Inception?")
    assert not any(ex.values())
    assert not any(mem.extract_preferences("Recommend something for me").values())


def test_user_context_is_compact():
    ctx = mem.build_user_context({"preferred_genres": ["Thriller"], "disliked_genres": ["Animation"]},
                                 [{"interaction_type": "liked", "title": "Inception"}],
                                 [{"title": "Se7en", "year": 1995}])
    assert "Thriller" in ctx and "Animation" in ctx and "Inception" in ctx and "Se7en" in ctx and len(ctx) < 500


LIVE = pytest.mark.skipif(not (settings.LLM_API_KEY and settings.TMDB_API_KEY), reason="LLM/TMDB keys not set")


@pytest.mark.live
@LIVE
@pytest.mark.parametrize("prompt,tool", [
    ("Tell me about Interstellar", "search_movie"),
    ("What movies are trending right now?", "trending_movies"),
    ("Give me 5 action movies rated above 8", "recommend_movies"),
    ("What movies are in The Godfather series?", "movie_collection"),
    ("hello!", None),
])
def test_tool_selection(prompt, tool):
    from agent import run_agent
    from tools import ToolContext
    out = run_agent(prompt, [], "USER PROFILE: No stored preferences yet.", ToolContext())
    assert out["tool"] == tool
