"""Supabase PostgREST clients (lightweight, connection-reusing).

public_client()   anon key               -> public tables (movies, trending_movies, new_movies)
user_client(jwt)  anon key + user's JWT  -> Row Level Security applies, user only sees own rows
service_client()  service-role key       -> ONLY for jobs/ (sync). The web routes never use it.
"""
from functools import lru_cache

from postgrest import SyncPostgrestClient

from config import settings


def _make(api_key: str, bearer: str | None = None) -> SyncPostgrestClient:
    return SyncPostgrestClient(
        f"{settings.SUPABASE_URL}/rest/v1",
        headers={"apikey": api_key, "Authorization": f"Bearer {bearer or api_key}"},
        timeout=15,
    )


@lru_cache(maxsize=1)
def public_client() -> SyncPostgrestClient:
    return _make(settings.SUPABASE_ANON_KEY)


@lru_cache(maxsize=64)
def user_client(jwt: str) -> SyncPostgrestClient:
    return _make(settings.SUPABASE_ANON_KEY, jwt)


@lru_cache(maxsize=1)
def service_client() -> SyncPostgrestClient:
    if not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required for sync jobs")
    return _make(settings.SUPABASE_SERVICE_ROLE_KEY)
