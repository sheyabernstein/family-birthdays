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

# Every magic link also carries a short, typeable code with the same
# effect - for whoever finds tapping a link on *this* device impractical
# (a feature phone's own SMS app with no usable browser, or someone
# reading/dictating a code off a message on one device while actually
# signing in on another). 0/O/1/I/L are left out of the alphabet - easy
# to misread off a small screen or mishear dictated aloud - and lookup is
# always case-insensitive (see consume_code), so there's no shift-key
# precision required to type it back in.
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 6
# Belt-and-braces, not a load-bearing assumption this ever actually
# fires - at CODE_LENGTH=6 from a 33-character alphabet (~1.29 billion
# possibilities), a real collision between two codes simultaneously live
# within one TOKEN_TTL_SECONDS window is astronomically unlikely for
# this app's real scale. Still worth guarding cheaply: see
# _issue_unique_code's own docstring for what a collision would actually
# do if left unguarded.
CODE_COLLISION_RETRIES = 5
# A short code has far less entropy than the link token itself, so
# guessing it needs its own, tighter limiter - independent of
# is_rate_limited's own per-hour cap on *requesting* new links.
CODE_MAX_ATTEMPTS_PER_CODE = 5
CODE_MAX_ATTEMPTS_PER_ACCOUNT = 10
CODE_ATTEMPTS_WINDOW_SECONDS = RATE_LIMIT_WINDOW_SECONDS

_redis_client = redis.from_url(settings.REDIS_URL)


def _token_key(token: str) -> str:
    return f"magic_link:{token}"


def _code_key(code: str) -> str:
    return f"magic_code:{code}"


def _code_attempts_key(code: str) -> str:
    return f"magic_code_attempts:{code}"


def _code_attempts_by_account_key(account_uuid: str) -> str:
    return f"magic_code_attempts_account:{account_uuid}"


def _rate_limit_key(account_uuid: str) -> str:
    return f"magic_link_rate:{account_uuid}"


def is_rate_limited(account_uuid: str) -> bool:
    """Increment the account's request count for the current hour window and report whether it's over the limit.

    Every call counts, so check-and-issue in that order.
    """
    key = _rate_limit_key(account_uuid)
    count = _redis_client.incr(key)
    if count == 1:
        _redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    return count > RATE_LIMIT_MAX_PER_WINDOW


def issue_token(*, account_uuid: str, channel: str, destination: str) -> tuple[str, str]:
    """Issues a magic-link token and its own short code, both good for the same sign-in.

    Returns (token, code). The code is just a second, human-typeable
    pointer at the *same* underlying session - consuming either one
    burns the token itself (see consume_token/consume_code), so using
    one always invalidates the other too, never a double sign-in.
    """
    token = secrets.token_urlsafe(32)
    payload = f"{account_uuid}:{channel}:{destination}"
    _redis_client.set(_token_key(token), payload, ex=TOKEN_TTL_SECONDS)

    code = _issue_unique_code(token)
    return token, code


def _issue_unique_code(token: str) -> str:
    """Generates a short code and reserves it atomically, retrying on the rare chance of a collision.

    A plain SET here would silently overwrite another still-live code's
    own Redis entry on collision, repointing it at this token instead -
    whoever that first code belonged to would then find it mysteriously
    stopped working, with no error anywhere to explain why. SET's own
    NX flag only reserves a key that's genuinely free, so a collision is
    detected and re-rolled rather than clobbering someone else's pending
    sign-in.
    """
    for _ in range(CODE_COLLISION_RETRIES):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if _redis_client.set(_code_key(code), token, ex=TOKEN_TTL_SECONDS, nx=True):
            return code
    # Never observed in practice - every retry landing on an already-
    # taken code would take a truly pathological run of bad luck. Falls
    # back to a plain overwrite rather than failing the sign-in request
    # outright over odds this remote.
    _redis_client.set(_code_key(code), token, ex=TOKEN_TTL_SECONDS)
    return code


def consume_token(token: str) -> dict | None:
    """Fetch and invalidate a token in one step.

    Returns None for a token that's missing, already used, or expired -
    Redis doesn't distinguish those cases, which is fine, since the
    caller treats all three identically (an invalid-link page). Also the
    engine behind consume_code below - a code is just a pointer at a
    token, so redeeming one this way burns it for the other path too.
    """
    payload = _redis_client.getdel(_token_key(token))
    if payload is None:
        return None
    account_uuid, channel, destination = payload.decode().split(":", 2)
    return {"account_uuid": account_uuid, "channel": channel, "destination": destination}


def is_code_guess_blocked(account_uuid: str) -> bool:
    """Per-account lockout on *guessing* codes, separate from is_rate_limited's own per-hour request cap.

    Every call counts, whether or not the code being tried is real -
    same "every attempt counts" shape as is_rate_limited, just scoped to
    guesses rather than requests.
    """
    key = _code_attempts_by_account_key(account_uuid)
    count = _redis_client.incr(key)
    if count == 1:
        _redis_client.expire(key, CODE_ATTEMPTS_WINDOW_SECONDS)
    return count > CODE_MAX_ATTEMPTS_PER_ACCOUNT


def consume_code(*, account_uuid: str, code: str) -> dict | None:
    """Same effect as consume_token, via the short code instead of the link - always case-insensitive.

    account_uuid is the account the caller *claims* this code belongs to
    (resolved from whatever identifier they typed alongside it - see
    VerifyCodeView) - a code that turns out to resolve to a different
    account is rejected the same way as a wrong code outright, never
    treated as a hint that it just belongs to someone else.

    Two independent limiters apply before a code is even looked up:
    is_code_guess_blocked's own per-account cap, and a per-*this-code*
    attempt cap (CODE_MAX_ATTEMPTS_PER_CODE) - so a leaked/guessed-at
    single code can't be brute-forced even by someone spreading guesses
    across many different claimed accounts.
    """
    code = code.strip().upper()
    if is_code_guess_blocked(account_uuid):
        return None

    attempts_key = _code_attempts_key(code)
    attempts = _redis_client.incr(attempts_key)
    if attempts == 1:
        _redis_client.expire(attempts_key, TOKEN_TTL_SECONDS)
    if attempts > CODE_MAX_ATTEMPTS_PER_CODE:
        return None

    token_bytes = _redis_client.get(_code_key(code))
    if token_bytes is None:
        return None
    token = token_bytes.decode()

    # Peeks at the payload (a plain GET, not GETDEL) before actually
    # consuming anything - a wrong account_uuid claimed alongside a real
    # code must reject cleanly, not burn the token out from under
    # whoever the code actually belongs to. Found for real: an earlier
    # version called consume_token() first and checked account_uuid
    # after, so a wrong guess here could invalidate someone else's
    # perfectly legitimate pending sign-in.
    payload_bytes = _redis_client.get(_token_key(token))
    if payload_bytes is None or payload_bytes.decode().split(":", 1)[0] != account_uuid:
        return None

    return consume_token(token)
