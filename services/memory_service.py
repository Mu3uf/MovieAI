"""User memory (Supabase, all queries go through the USER's client so RLS protects the data).

Short-term: last few messages of the conversation.
Long-term : user_preferences + user_movie_interactions, rendered as a compact text block.
Preference extraction is deterministic (regex) - no LLM call per message."""
import logging
import re
from datetime import datetime, timezone

from services import movie_service as ms, tmdb_service as tmdb

log = logging.getLogger("memory")
MAX_LIST = 20
INTERACTION_TYPES = ("watched", "liked", "disliked", "recommended", "saved")
MOOD_TAGS = ("psychological", "dark", "mind-bending", "gritty", "feel-good", "slow-burn", "serious")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ conversations / messages
def create_conversation(db, user_id: str, title: str) -> dict:
    row = {"user_id": user_id, "title": (title or "New chat")[:60]}
    return db.from_("conversations").insert(row).execute().data[0]


def get_conversation(db, conv_id: str) -> dict | None:
    rows = db.from_("conversations").select("id,title").eq("id", conv_id).limit(1).execute().data
    return rows[0] if rows else None  # RLS: someone else's conversation looks like "not found"


def list_conversations(db, limit: int = 30) -> list[dict]:
    return (db.from_("conversations").select("id,title,updated_at")
            .order("updated_at", desc=True).limit(limit).execute().data)


def recent_messages(db, conv_id: str, limit: int) -> list[dict]:
    rows = (db.from_("messages").select("role,content,metadata").eq("conversation_id", conv_id)
            .order("created_at", desc=True).limit(limit).execute().data)
    return list(reversed(rows))


def all_messages(db, conv_id: str, limit: int = 100) -> list[dict]:
    return (db.from_("messages").select("role,content,metadata,created_at").eq("conversation_id", conv_id)
            .order("created_at").limit(limit).execute().data)


def save_messages(db, user_id: str, conv_id: str, user_text: str, reply: str, movies: list[dict],
                  heading: str | None) -> None:
    slim = [{k: m.get(k) for k in ("id", "title", "rating", "vote_count", "release_date", "year",
                                   "poster_path", "poster_url", "overview", "genres")} for m in movies]
    db.from_("messages").insert([
        {"conversation_id": conv_id, "user_id": user_id, "role": "user", "content": user_text,"metadata": {}},
        {"conversation_id": conv_id, "user_id": user_id, "role": "assistant", "content": reply,
         "metadata": {"movies": slim, "heading": heading} if slim else {}},
    ]).execute()
    db.from_("conversations").update({"updated_at": _now()}).eq("id", conv_id).execute()


def shown_movies(history: list[dict], last_n_assistant: int = 3) -> list[dict]:
    """Movies displayed in the last few assistant messages (for 'these' / 'different ones')."""
    out, count = [], 0
    for msg in reversed(history):
        if msg["role"] != "assistant":
            continue
        out += (msg.get("metadata") or {}).get("movies", [])
        count += 1
        if count >= last_n_assistant:
            break
    return out


# ------------------------------------------------------------------ preferences
def get_preferences(db, user_id: str) -> dict:
    rows = db.from_("user_preferences").select("*").eq("user_id", user_id).limit(1).execute().data
    return rows[0] if rows else {}


def _add(lst, items):
    lst = list(lst or [])
    for it in items:
        if it.lower() not in [x.lower() for x in lst]:
            lst.append(it)
    return lst[-MAX_LIST:]


def _remove(lst, items):
    drop = {i.lower() for i in items}
    return [x for x in (lst or []) if x.lower() not in drop]


_NEG = re.compile(r"\b(hate|hated|dislike|disliked|can'?t stand|cannot stand|(?:do not|don'?t) (?:really )?(?:like|enjoy|want)|not a fan of|sick of|tired of|avoid)\b", re.I)
_NEG_IMPERATIVE = re.compile(r"\b(?:don'?t|do not|never) (?:give|show|recommend|suggest)\b", re.I)
_POS = re.compile(r"\b(love|loved|like|liked|enjoy|enjoyed|prefer|adore|adored|into|fan of|favou?rite)\b", re.I)
_FIRST = re.compile(r"\b(i|i'm|im|i've|ive|i'd|my)\b", re.I)
_TITLE = re.compile(r"\bi\s+(?:really\s+|absolutely\s+|also\s+|just\s+)*(loved|liked|enjoyed|adored|hated|disliked)\s+(?:watching\s+)?(?:the\s+(?:movie|film)\s+)?(.+)$", re.I)
_FAV = re.compile(r"\bmy\s+favou?rite\s+(?:movie|film)\s+is\s+(.+)$", re.I)
_GENRE_HINT = re.compile(r"\b(movies?|films?|genre|stuff|ones|kind)\b", re.I)


def extract_preferences(text: str) -> dict:
    """Deterministic extraction of clear statements like 'I hate horror' / 'I loved Inception'."""
    out = {"like_genres": [], "dislike_genres": [], "like_movies": [], "dislike_movies": []}
    text = text.replace("\u2019", "'")
    for s in re.split(r"[.!;\n]+|\s+but\s+|\s+and\s+(?=i\b)", text, flags=re.I):
        s = s.strip()
        if not s or "?" in s:
            continue
        neg_imp = bool(_NEG_IMPERATIVE.search(s))
        if not (_FIRST.search(s) or neg_imp):
            continue

        m = _FAV.search(s)
        verb = None
        if m:
            cap, negative = m.group(1), False
        else:
            m = _TITLE.search(s)
            cap = m.group(2) if m else None
            negative = bool(m) and m.group(1).lower() in ("hated", "disliked")
        if m and cap:
            cap = re.sub(r"\s+(so much|a lot|too|very much)$", "", cap.strip(" '\"")).strip(" '\"")
            ids, _ = ms.parse_genres(cap)
            looks_genre = bool(_GENRE_HINT.search(cap)) or cap.lower() in ms._GENRE_TABLE
            if cap and any(w[:1].isupper() for w in cap.split()) and len(cap.split()) <= 7 and not looks_genre:
                (out["dislike_movies"] if negative else out["like_movies"]).append(cap)
                continue

        negative = bool(_NEG.search(s)) or neg_imp
        positive = (not negative) and bool(_POS.search(s))
        if not (negative or positive):
            continue
        ids, _ = ms.parse_genres(s)
        names = [tmdb.GENRES[i] for i in ids]
        names += [t.capitalize() for t in MOOD_TAGS if t in s.lower()]
        (out["dislike_genres"] if negative else out["like_genres"]).extend(names)
    return out


def build_summary(p: dict) -> str:
    parts = []
    if p.get("preferred_genres"):
        parts.append("Prefers " + ", ".join(p["preferred_genres"][:6]))
    if p.get("disliked_genres"):
        parts.append("dislikes " + ", ".join(p["disliked_genres"][:6]))
    if p.get("favorite_movies"):
        parts.append("favorite movies: " + ", ".join(p["favorite_movies"][:5]))
    return ("; ".join(parts) + ".") if parts else ""


def apply_text_preferences(db, user_id: str, prefs: dict, text: str) -> dict:
    """Update stored preferences only when the message contains a clear statement."""
    ex = extract_preferences(text)
    if not any(ex.values()):
        return prefs
    new = dict(prefs)
    pg, dg = list(new.get("preferred_genres") or []), list(new.get("disliked_genres") or [])
    pg = _add(_remove(pg, ex["dislike_genres"]), ex["like_genres"])
    dg = _add(_remove(dg, ex["like_genres"]), ex["dislike_genres"])
    fm, dm = list(new.get("favorite_movies") or []), list(new.get("disliked_movies") or [])
    fm = _add(_remove(fm, ex["dislike_movies"]), ex["like_movies"])
    dm = _add(_remove(dm, ex["like_movies"]), ex["dislike_movies"])
    new.update(preferred_genres=pg, disliked_genres=dg, favorite_movies=fm, disliked_movies=dm)
    new["preference_summary"] = build_summary(new)
    row = {"user_id": user_id, "preferred_genres": pg, "disliked_genres": dg, "favorite_movies": fm,
           "disliked_movies": dm, "preference_summary": new["preference_summary"], "updated_at": _now()}
    try:
        db.from_("user_preferences").upsert(row, on_conflict="user_id").execute()
    except Exception:
        log.exception("could not save preferences")
        return prefs
    return new


# ------------------------------------------------------------------ interactions
def list_interactions(db, limit: int = 60) -> list[dict]:
    return (db.from_("user_movie_interactions").select("tmdb_id,interaction_type,title,created_at")
            .order("created_at", desc=True).limit(limit).execute().data)


def toggle_interaction(db, user_id: str, tmdb_id: int, kind: str, title: str | None,
                       poster_path: str | None) -> bool:
    """Click once = on, click again = off. liked and disliked exclude each other. Returns new state."""
    t = db.from_("user_movie_interactions")
    existing = t.select("id").eq("tmdb_id", tmdb_id).eq("interaction_type", kind).limit(1).execute().data
    if existing:
        db.from_("user_movie_interactions").delete().eq("id", existing[0]["id"]).execute()
        return False
    opposite = {"liked": "disliked", "disliked": "liked"}.get(kind)
    if opposite:
        db.from_("user_movie_interactions").delete().eq("tmdb_id", tmdb_id).eq("interaction_type", opposite).execute()
    db.from_("user_movie_interactions").insert({
        "user_id": user_id, "tmdb_id": tmdb_id, "interaction_type": kind,
        "title": (title or "")[:200], "poster_path": poster_path}).execute()
    return True


# ------------------------------------------------------------------ compact context for the LLM
def build_user_context(prefs: dict, interactions: list[dict], last_shown: list[dict]) -> str:
    def titles(kind, k=5):
        return [i["title"] for i in interactions if i["interaction_type"] == kind and i.get("title")][:k]

    lines = []
    if prefs.get("preferred_genres"):
        lines.append("Preferred genres: " + ", ".join(prefs["preferred_genres"][:8]))
    if prefs.get("disliked_genres"):
        lines.append("Disliked genres: " + ", ".join(prefs["disliked_genres"][:8]))
    if prefs.get("favorite_movies"):
        lines.append("Favorite movies: " + ", ".join(prefs["favorite_movies"][:5]))
    for label, kind in (("Liked", "liked"), ("Recently watched", "watched"),
                        ("Disliked movies", "disliked"), ("Saved", "saved")):
        t = titles(kind)
        if t:
            lines.append(f"{label}: " + ", ".join(t))
    body = "\n".join(lines) if lines else "No stored preferences yet."
    ctx = "USER PROFILE (stored in the database):\n" + body
    if last_shown:
        shown = ", ".join(f"{m['title']} ({m.get('year') or '?'})" for m in last_shown[:8])
        ctx += "\nMovies most recently shown to the user (what 'these'/'them' refers to): " + shown
    return ctx
