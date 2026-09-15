"""Magic-link tokens, stored in Redis instead of a DB table.

A login token is single-use and short-lived by nature - Redis's native
key expiry (TTL) is exactly that, for free, with no stale-row cleanup job
needed. GETDEL makes "fetch and invalidate" atomic, so a token can't be
raced into being used twice.
"""

import secrets

import redis
from django.conf import settings

TOKEN_TTL_SECONDS = 15 * 60
RATE_LIMIT_WINDOW_SECONDS = 60 * 60
RATE_LIMIT_MAX_PER_WINDOW = 5

_redis_client = redis.from_url(settings.REDIS_URL)


def _token_key(token: str) -> str:
    return f"magic_link:{token}"


def _rate_limit_key(account_id: int) -> str:
    return f"magic_link_rate:{account_id}"


def is_rate_limited(account_id: int) -> bool:
    """Increment the account's request count for the current hour window and report whether it's over the limit.

    Every call counts, so check-and-issue in that order.
    """
    key = _rate_limit_key(account_id)
    count = _redis_client.incr(key)
    if count == 1:
        _redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    return count > RATE_LIMIT_MAX_PER_WINDOW


def issue_token(*, account_id: int, channel: str, destination: str) -> str:
    token = secrets.token_urlsafe(32)
    payload = f"{account_id}:{channel}:{destination}"
    _redis_client.set(_token_key(token), payload, ex=TOKEN_TTL_SECONDS)
    return token


def consume_token(token: str) -> dict | None:
    """Fetch and invalidate a token in one step.

    Returns None for a token that's missing, already used, or expired -
    Redis doesn't distinguish those cases, which is fine, since the
    caller treats all three identically (an invalid-link page).
    """
    payload = _redis_client.getdel(_token_key(token))
    if payload is None:
        return None
    account_id, channel, destination = payload.decode().split(":", 2)
    return {"account_id": int(account_id), "channel": channel, "destination": destination}
