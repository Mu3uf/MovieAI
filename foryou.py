"""'For You' page: personalised picks from the user's liked / watched / saved / disliked movies.
The taste is computed inside Supabase (SQL function for_you) from the movies' stored embeddings,
so it is always up to date and needs no embedding model on the web server."""
import logging

from flask import Blueprint, g, jsonify

from database.auth import login_required
from database.supabase_db import user_client
from services import movie_service as ms

log = logging.getLogger("foryou")
bp = Blueprint("foryou", __name__)


@bp.get("/api/for-you")
@login_required
def for_you():
    try:
        rows = user_client(g.token).rpc("for_you", {"match_count": 24}).execute().data or []
    except Exception:
        log.exception("for_you failed")
        return jsonify(error="Could not build your For You list right now."), 503
    movies = []
    for r in rows:
        card = ms.to_card(ms._db_to_raw(r))
        card["because"] = r.get("because")
        movies.append(card)
    message = None if movies else (
        "Like a few movies or mark them as Watched, and your For You list will appear here.")
    return jsonify(movies=movies, message=message)