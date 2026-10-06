"""The single AI agent: LLM + 4 tools, a small explicit tool-calling loop.

Why not AgentExecutor? A 20-line loop is easier to debug, has a hard cap on LLM calls (speed,
token usage, rate limits) and does not break when LangChain changes its agent APIs."""
import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from services.llm_service import get_llm
from tools import ToolContext, build_tools

log = logging.getLogger("agent")
MAX_LLM_CALLS = 3
MAX_TOOL_CALLS = 1

SYSTEM_PROMPT = """You are CineMind, the movie assistant of a web app. Movie data comes ONLY from tools (TMDB).
Never state ratings, dates, plots, cast or movie lists from your own memory.

TOOLS - call at most one per user request, and none when no movie data is needed:
- search_movie: facts about ONE specific movie ("tell me about X", "what is X about", "is X good").
- recommend_movies: any request for suggestions or lists: genre, year range, minimum rating, language,
  actor, director, mood, or "similar to X" (use similar_to). Convert EVERY stated constraint into arguments
  (e.g. "5 action movies rated above 8 from 2010 to 2020, no animation" -> genre, min_rating, start_year,
  end_year, exclude_genre, number_of_movies).
- trending_movies: what is trending / popular right now.
- movie_collection: all movies of a series or franchise, in release order.
- my_movies: the user's OWN activity: movies they liked, watched, saved or disliked ("show my liked movies",
  "what did I watch", "the highest rated movie I watched"). Leave limit empty to show ALL of them; use
  sort_by="rating" and limit=1 (or the requested number) for "highest rated". Never answer such questions
  from USER PROFILE (it only lists a few titles); always call my_movies.
Do not call a tool for greetings, thanks, questions about the user's own profile (answer from USER PROFILE),
or off-topic chat.

RULES

0. Choose the tool from the user's LATEST message only. Earlier messages are context for words like
   "these" or "different", never a reason to repeat an earlier tool call. A request with a genre, rating,
   year range, or a number of movies ALWAYS goes to recommend_movies, never search_movie.
1. The user's current request ALWAYS overrides stored preferences. Use the stored profile only when the
   request is open ("recommend something for me") or does not contradict it.
2. "Recommend something for me" -> recommend_movies with no genre and use_user_preferences=true.
3. Follow-ups: "different/other/more ones" -> same constraints + exclude_previous=true.
   "I don't like these" -> exclude_previous=true and pick a clearly different angle.
   "Something darker/lighter/funnier" -> keep earlier constraints, set mood_query and exclude_previous=true.
   Use the last shown movies listed in the context to resolve "these" and "that one".
4. If a tool returns fewer movies than requested, say so honestly. If it returns none, say that no movie
   fits and suggest relaxing ONE constraint. Never invent movies to fill the gap.
5. If a tool returns an error, apologise briefly and suggest trying again.
6. The movie cards are displayed to the user separately. Write 1-3 short sentences: what you found and why it
   fits. You may name one or two titles. Never output JSON, tables, ids or long lists.
7. Only discuss movies and TV-film topics; politely steer other topics back to movies.
8. Tool arguments must be in English. Reply in the same language as the user's last message.
"""


class AgentError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _text(ai: AIMessage) -> str:
    c = ai.content
    if isinstance(c, list):
        c = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return (c or "").strip()


def _is_tool_failure(e: Exception) -> bool:
    t = str(e).lower()
    return "tool_use_failed" in t or "failed to call a function" in t or "tool choice is none" in t


def _invoke(llm, msgs, ctx: ToolContext, retry: bool = True):
    last = None
    for attempt in range(2 if retry else 1):
        try:
            with ctx.timings.step("llm"):
                return llm.invoke(msgs)
        except Exception as e:
            last = e
            if retry and attempt == 0 and _is_tool_failure(e):
                log.warning("malformed tool call, retrying once")
                continue
            break
    raise last


def run_agent(message: str, history: list[dict], user_context: str, ctx: ToolContext) -> dict:
    from services.llm_service import friendly_llm_error

    reply = ""
    try:
        llm = get_llm()
        tools = build_tools(ctx)
        by_name = {t.name: t for t in tools}
        bound = llm.bind_tools(tools)

        msgs = [SystemMessage(SYSTEM_PROMPT), SystemMessage(user_context)]
        if history:
            lines = [("User: " if h["role"] == "user" else "Assistant: ")
                     + (h["content"] or "")[:400].replace("\n", " ") for h in history]
            msgs.append(SystemMessage(
                "EARLIER MESSAGES (context only; they were already answered. "
                "Never repeat their tool calls):\n" + "\n".join(lines)))
        msgs.append(HumanMessage(message))

        for step in range(MAX_LLM_CALLS):
            final_step = step == MAX_LLM_CALLS - 1
            if final_step:
                msgs.append(SystemMessage(
                    "Answer the user now in 1-3 sentences using the tool results above. Do not call any tool."))
                try:
                    ai = _invoke(llm, msgs, ctx, retry=False)
                except Exception as e:
                    if _is_tool_failure(e):
                        log.warning("final answer step tried to call a tool; using fallback text")
                        break
                    raise
            else:
                ai = _invoke(bound, msgs, ctx)

            calls = getattr(ai, "tool_calls", None) or []
            text = _text(ai)
            log.info("agent step %d: tool_calls=%d reply_chars=%d", step, len(calls), len(text))

            if not calls:
                reply = text
                break

            msgs.append(ai)
            for call in calls:
                name, args = call["name"], call.get("args") or {}
                log.info("LLM chose tool=%s args=%s", name, args)
                if len(ctx.tools_called) >= MAX_TOOL_CALLS:
                    log.warning("skipping %s: tool limit (%d) reached", name, MAX_TOOL_CALLS)
                    out = json.dumps({"error": "tool limit reached, answer with the results you have"})
                elif name not in by_name:
                    log.warning("skipping unknown tool %s", name)
                    out = json.dumps({"error": "unknown tool"})
                else:
                    try:
                        out = by_name[name].invoke(args)
                    except Exception as e:
                        log.warning("tool %s rejected args %s: %s", name, args, e)
                        out = json.dumps({"error": "invalid arguments, answer without this tool"})
                msgs.append(ToolMessage(content=out, tool_call_id=call["id"], name=name))
    except AgentError:
        raise
    except Exception as e:
        log.exception("agent failed")
        message_, status = friendly_llm_error(e)
        raise AgentError(message_, status) from e

    final = ctx.final()
    if not reply:
        log.warning("empty LLM reply, using fallback text")
        n = len(final.get("movies", []))
        reply = final.get("message") or (
            f"Here are {n} result(s): {final.get('heading') or 'your movies'}." if n
            else "I couldn't find anything for that. Could you rephrase?")
    return {
        "reply": reply,
        "movies": final.get("movies", []),
        "heading": final.get("heading"),
        "tool": ctx.tools_called[-1] if ctx.tools_called else None,
        "notice": final.get("message") if not final.get("movies") else None,
    }