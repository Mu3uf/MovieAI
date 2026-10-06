"""Authentication: the browser logs in with Supabase Auth and sends its access token as
`Authorization: Bearer <jwt>`. We verify the token with Supabase and take the user id from
the verified result - a user_id sent by the frontend is never trusted."""
import logging
import time
from functools import wraps

import httpx
from flask import g, jsonify, request

from config import settings

log = logging.getLogger("auth")
_http = httpx.Client(timeout=10)
_cache: dict[str, tuple[float, dict]] = {}  # token -> (expires_at, user)
_TTL = 60


class AuthError(Exception):
    pass


def verify_token(token: str) -> dict:
    now = time.time()
    hit = _cache.get(token)
    if hit and hit[0] > now:
        return hit[1]
    try:
        r = _http.get(
            f"{settings.SUPABASE_URL}/auth/v1/user",
            headers={"apikey": settings.SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as e:
        log.error("Supabase auth unreachable: %s", e)
        raise AuthError("Authentication service unavailable") from e
    if r.status_code != 200:
        raise AuthError("Invalid or expired session")
    data = r.json()
    user = {"id": data["id"], "email": data.get("email")}
    if len(_cache) > 500:
        _cache.clear()
    _cache[token] = (now + _TTL, user)
    return user


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header) < 20:
            return jsonify(error="Please log in."), 401
        token = header[7:].strip()
        try:
            g.user = verify_token(token)
        except AuthError as e:
            return jsonify(error=str(e)), 401
        g.token = token
        return fn(*args, **kwargs)

    return wrapper
