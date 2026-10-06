"""Scheduled sync TMDB -> Supabase. Uses the SERVICE ROLE key (run from cron / GitHub Actions only).

  python -m jobs.sync_movies --trending --new          every few hours
  python -m jobs.sync_movies --movies 5                daily (5 pages x 2 lists ~ 200 movies)
  python -m jobs.sync_movies --embed                   once, after installing fastembed (optional)
"""
import argparse
import logging
from datetime import datetime, timezone

from database.supabase_db import service_client
from services import tmdb_service as tmdb

log = logging.getLogger("sync")
from config import settings  # noqa: E402  (configures logging)


def _names(m):
    return [tmdb.GENRES[i] for i in (m.get("genre_ids") or []) if i in tmdb.GENRES]


def _movie_row(m, ts):
    if not m.get("id") or not m.get("title"):
        return None
    return {
        "tmdb_id": m["id"], "title": m["title"], "overview": m.get("overview") or "",
        "release_date": m.get("release_date") or None, "rating": m.get("vote_average"),
        "vote_count": m.get("vote_count"), "popularity": m.get("popularity"),
        "poster_path": m.get("poster_path"), "backdrop_path": m.get("backdrop_path"),
        "genres": _names(m), "original_language": m.get("original_language"), "updated_at": ts,
    }


def _slim_row(m, ts):
    if not m.get("id") or not m.get("title"):
        return None
    return {"tmdb_id": m["id"], "title": m["title"], "rating": m.get("vote_average"),
            "release_date": m.get("release_date") or None, "poster_path": m.get("poster_path"),
            "overview": m.get("overview") or "", "fetched_at": ts}


def sync_trending(db, ts):
    items = [r for r in (_slim_row(m, ts) for m in tmdb.trending("week")[:20]) if r]
    for i, r in enumerate(items, 1):
        r["position"] = i
    if not items:
        raise RuntimeError("TMDB returned no trending movies; cache left untouched")
    db.from_("trending_movies").upsert(items, on_conflict="position").execute()
    db.from_("trending_movies").delete().lt("fetched_at", ts).execute()
    log.info("trending: %d rows", len(items))


def sync_new(db, ts):
    items = [r for r in (_slim_row(m, ts) for m in tmdb.now_playing()) if r and r["release_date"]]
    if not items:
        raise RuntimeError("TMDB returned no new movies; cache left untouched")
    db.from_("new_movies").upsert(items, on_conflict="tmdb_id").execute()
    db.from_("new_movies").delete().lt("fetched_at", ts).execute()
    log.info("new movies: %d rows", len(items))


def sync_movies(db, ts, pages):
    total = 0
    for fn in (tmdb.popular, tmdb.top_rated):
        for page in range(1, pages + 1):
            try:
                rows = [r for r in (_movie_row(m, ts) for m in fn(page)) if r]
                if rows:
                    db.from_("movies").upsert(rows, on_conflict="tmdb_id").execute()  # no duplicates
                    total += len(rows)
            except Exception:
                log.exception("page %s of %s failed - continuing", page, fn.__name__)
    log.info("movies upserted: %d", total)


def sync_embeddings(db, batch=50):
    from services.movie_service import embed_texts

    done = 0
    while True:
        rows = (db.from_("movies").select("tmdb_id,title,overview,genres").is_("embedding", "null")
                .limit(batch).execute().data)
        if not rows:
            break
        texts = [f"{r['title']}. {', '.join(r.get('genres') or [])}. {r.get('overview') or ''}" for r in rows]
        vecs = embed_texts(texts)
        db.from_("movies").upsert([{"tmdb_id": r["tmdb_id"], "title": r["title"], "embedding": v}
                                   for r, v in zip(rows, vecs)], on_conflict="tmdb_id").execute()
        done += len(rows)
        log.info("embedded %d movies", done)
    log.info("embeddings done: %d", done)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trending", action="store_true")
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--movies", type=int, metavar="PAGES", help="pages per list (20 movies/page)")
    ap.add_argument("--embed", action="store_true")
    args = ap.parse_args()
    if not (args.trending or args.new or args.movies or args.embed):
        ap.error("choose at least one of --trending --new --movies N --embed")

    db, ts, failed = service_client(), datetime.now(timezone.utc).isoformat(), False
    steps = []
    if args.trending: steps.append(("trending", lambda: sync_trending(db, ts)))
    if args.new: steps.append(("new", lambda: sync_new(db, ts)))
    if args.movies: steps.append(("movies", lambda: sync_movies(db, ts, args.movies)))
    if args.embed: steps.append(("embed", lambda: sync_embeddings(db)))
    for name, fn in steps:
        try:
            fn()
        except Exception:
            failed = True
            log.exception("sync step '%s' failed", name)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
