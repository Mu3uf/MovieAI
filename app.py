"""Flask API. Routes stay thin; the logic lives in services/, tools/ and agent.py."""
import logging
import time
import uuid
from collections import defaultdict, deque

from flask import Flask, g, jsonify, render_template, request

from agent import AgentError, run_agent
from config import settings
from database.auth import login_required
from database.supabase_db import user_client
from services import memory_service as memory, movie_service
from services.timing import Timings
from tools import ToolContext
from foryou import bp as foryou_bp
log = logging.getLogger("app")
app = Flask(__name__)
app.register_blueprint(foryou_bp)

if settings.missing():
    log.warning("Missing environment variables: %s (see .env.example)", ", ".join(settings.missing()))

_home_cache = {"t": 0.0, "data": None}
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited(user_id: str, limit: int = 20, window: int = 60) -> bool:
    q, now = _hits[user_id], time.time()
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _valid_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


@app.errorhandler(404)
def not_found(_):
    if request.path.startswith("/api/"):
        return jsonify(error="Not found."), 404
    return "Not found", 404


@app.errorhandler(Exception)
def unexpected(e):
    log.exception("unhandled error")  # full trace stays in the server log
    return jsonify(error="Something went wrong. Please try again."), 500


# ------------------------------------------------------------------ pages / public
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def public_config():
    # The anon key is PUBLIC by design (protected by RLS). Never expose the service-role key.
    return jsonify(supabase_url=settings.SUPABASE_URL, supabase_anon_key=settings.SUPABASE_ANON_KEY)


@app.get("/api/health")
def health():
    return jsonify(ok=not settings.missing(), missing=settings.missing(),
                   llm=f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}")


@app.get("/api/home")
def home():
    """Trending + new movies from the Supabase cache; memoised for 5 minutes in-process."""
    if _home_cache["data"] and time.time() - _home_cache["t"] < 300:
        return jsonify(_home_cache["data"])
    try:
        data = {"trending": movie_service.get_trending(12), "new": movie_service.get_new(12)}
    except Exception:
        log.exception("home failed")
        return jsonify(error="Could not load movies right now."), 502
    _home_cache.update(t=time.time(), data=data)
    return jsonify(data)


# ------------------------------------------------------------------ chat
@app.post("/api/chat")
@login_required
def chat():
    t = Timings()
    body = request.get_json(silent=True) or {}
    text = str(body.get("message") or "").strip()
    conv_id = body.get("conversation_id")
    if not text:
        return jsonify(error="Please type a message."), 400
    if len(text) > 1000:
        return jsonify(error="Message is too long (max 1000 characters)."), 400
    if conv_id and not _valid_uuid(conv_id):
        return jsonify(error="Invalid conversation."), 400

    uid = g.user["id"]  # from the verified token, never from the request body
    if _rate_limited(uid):
        return jsonify(error="Slow down a little - too many messages. Try again in a minute."), 429
    db = user_client(g.token)

    try:
        with t.step("db_load"):
            if conv_id:
                if not memory.get_conversation(db, conv_id):
                    return jsonify(error="Conversation not found."), 404
            else:
                conv_id = memory.create_conversation(db, uid, text[:50])["id"]
            history = memory.recent_messages(db, conv_id, settings.HISTORY_MESSAGES)
            prefs = memory.apply_text_preferences(db, uid, memory.get_preferences(db, uid), text)
            interactions = memory.list_interactions(db)
    except Exception:
        log.exception("database error while loading context")
        return jsonify(error="The database is not reachable right now. Please try again."), 503

    shown = memory.shown_movies(history)
    ctx = ToolContext(
        prefs=prefs, timings=t, db=db, message=text,
        seen_ids={m["id"] for m in shown},
        interacted_ids={i["tmdb_id"] for i in interactions if i["interaction_type"] in ("watched", "disliked")},
    )
    user_context = memory.build_user_context(prefs, interactions, shown)

    try:
        out = run_agent(text, [{"role": h["role"], "content": h["content"]} for h in history], user_context, ctx)
    except AgentError as e:
        return jsonify(error=str(e), conversation_id=conv_id), e.status

    try:
        with t.step("db_save"):
            memory.save_messages(db, uid, conv_id, text, out["reply"], out["movies"], out["heading"])
    except Exception:
        log.exception("could not save messages")  # the answer is still returned

    return jsonify(conversation_id=conv_id, timings=t.summary(), **out)


@app.get("/api/conversations")
@login_required
def conversations():
    try:
        return jsonify(conversations=memory.list_conversations(user_client(g.token)))
    except Exception:
        log.exception("list conversations")
        return jsonify(error="Could not load conversations."), 503


@app.get("/api/conversations/<cid>")
@login_required
def conversation_messages(cid):
    if not _valid_uuid(cid):
        return jsonify(error="Invalid conversation."), 400
    try:
        db = user_client(g.token)
        if not memory.get_conversation(db, cid):
            return jsonify(error="Conversation not found."), 404
        return jsonify(messages=memory.all_messages(db, cid))
    except Exception:
        log.exception("load conversation")
        return jsonify(error="Could not load the conversation."), 503


# ------------------------------------------------------------------ like / dislike / watched / save
@app.get("/api/interactions")
@login_required
def interactions():
    try:
        rows = memory.list_interactions(user_client(g.token), 500)
        return jsonify(interactions=[{"tmdb_id": r["tmdb_id"], "interaction_type": r["interaction_type"]} for r in rows])
    except Exception:
        log.exception("list interactions")
        return jsonify(error="Could not load your activity."), 503


@app.post("/api/interactions")
@login_required
def interact():
    body = request.get_json(silent=True) or {}
    try:
        tmdb_id = int(body.get("tmdb_id"))
    except (TypeError, ValueError):
        return jsonify(error="Invalid movie."), 400
    kind = body.get("interaction_type")
    if tmdb_id <= 0 or kind not in ("watched", "liked", "disliked", "saved"):
        return jsonify(error="Invalid action."), 400
    try:
        active = memory.toggle_interaction(user_client(g.token), g.user["id"], tmdb_id, kind,
                                           str(body.get("title") or "")[:200], body.get("poster_path"))
        return jsonify(active=active)
    except Exception:
        log.exception("toggle interaction")
        return jsonify(error="Could not save that. Please try again."), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.PORT, debug=False)
