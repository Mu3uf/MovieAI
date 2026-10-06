"""Tools the agent can call. Each tool module has:
   run(...)       plain Python, returns a structured dict  -> easy to test without an LLM
   make_tool(ctx) wraps run() as a LangChain StructuredTool for the agent

ToolContext carries per-request state (user prefs, shown movies) and collects the structured
results so the backend can build movie cards WITHOUT asking the LLM to repeat movie data."""
import json
import logging
from dataclasses import dataclass, field

from services.timing import Timings
from services.tmdb_service import TMDBError

log = logging.getLogger("tools")


@dataclass
class ToolContext:
    prefs: dict = field(default_factory=dict)
    seen_ids: set = field(default_factory=set)         # movies shown earlier in this conversation
    interacted_ids: set = field(default_factory=set)   # watched / disliked by the user
    timings: Timings = field(default_factory=Timings)
    results: list = field(default_factory=list)
    message: str = ""
    db: object = None
    tools_called: list = field(default_factory=list)

    def call(self, name: str, fn, *args, **kwargs) -> str:
        """Run a tool function; never raises. Returns a SMALL json string for the LLM."""
        self.tools_called.append(name)
        try:
            with self.timings.step(f"tool:{name}"):
                result = fn(*args, **kwargs)
        except TMDBError as e:
            result = {"type": "error", "movies": [], "message": str(e)}
        except Exception:
            log.exception("tool %s crashed", name)
            result = {"type": "error", "movies": [], "message": "Something went wrong while looking that up."}
        result.setdefault("movies", [])
        self.results.append(result)
        return json.dumps(compact(result), ensure_ascii=False)

    def final(self) -> dict:
        """The result the UI should display: the last tool result that has movies, else the last result."""
        for r in reversed(self.results):
            if r.get("movies"):
                return r
        return self.results[-1] if self.results else {}


def compact(result: dict) -> dict:
    """What the LLM sees: ids/titles/years/ratings only (never posters, never full overviews)."""
    movies = result.get("movies", [])
    out = {"type": result.get("type"), "count": len(movies)}
    for k in ("message", "note", "collection"):
        if result.get(k):
            out[k] = result[k]
    out["movies"] = [{"id": m["id"], "title": m["title"], "year": m.get("year"), "rating": m.get("rating"),
                    "genres": (m.get("genres") or [])[:3]} for m in movies[:30]]
    if result.get("detail") and movies:
        m = movies[0]
        out["details"] = {"overview": m["overview"][:350], "genres": m["genres"],
                          "votes": m["vote_count"], "runtime_min": m.get("runtime"),
                          "tagline": m.get("tagline")}
    return out


def build_tools(ctx: ToolContext) -> list:
    from tools import collection_tool, my_movies_tool, recommendation_tool, search_movie_tool, trending_tool

    return [search_movie_tool.make_tool(ctx), recommendation_tool.make_tool(ctx),
            trending_tool.make_tool(ctx), collection_tool.make_tool(ctx), my_movies_tool.make_tool(ctx)]