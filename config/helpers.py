import os

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def get_env_bool(key: str, default: bool = False) -> bool:
    """Parse an env var as a bool, failing safe to `default` when unset or unparseable.

    Unset or blank falls back to `default`; anything else is compared
    against a fixed set of truthy tokens, case-insensitively. Deliberately
    not `val.lower() in _TRUE_VALUES or default` - that form makes any
    `default=True` setting impossible to turn off from the environment at
    all, since a falsy value like "False" still isn't in `_TRUE_VALUES` and
    the `or default` then substitutes True right back in (a real bug found
    here: `EMAIL_USE_TLS = get_env_bool("EMAIL_USE_TLS", True)` silently
    ignored an explicit `EMAIL_USE_TLS=False` in .env).
    """
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    return val.strip().lower() in _TRUE_VALUES


def get_env_int(key: str, default: int | None = None) -> int | None:
    """Parse an env var as an int, falling back to `default` rather than raising.

    Unset, blank, or not a valid int all fall back to `default`.
    """
    val = os.getenv(key, "").strip()

    try:
        return int(val)
    except ValueError:
        return default


def get_env_float(key: str, default: float | None = None) -> float | None:
    """Parse an env var as a float, falling back to `default` rather than raising.

    Unset, blank, or not a valid float all fall back to `default`.
    """
    val = os.getenv(key, "").strip()

    try:
        return float(val)
    except ValueError:
        return default


def get_env_list(key: str, default: list[str] | None = None, delimiter: str = ",") -> list[str]:
    """Split a delimited env var into stripped, non-empty strings.

    Unset or blank falls back to `default` (an empty list if none given).
    """
    default = default if default is not None else []

    val = os.getenv(key, "").strip()
    if not val:
        return default

    return [x.strip() for x in val.split(delimiter) if x.strip()]


def increment_counter(key: str, *, window_seconds: int) -> int:
    """Atomically increments a fixed-window counter, creating it at 1 if it doesn't exist yet.

    Redis's own INCR auto-vivifies a missing key at 0 before incrementing,
    but `cache.incr()` raises `ValueError` instead - this fills that gap
    with an add-then-incr fallback. `cache.add` is itself an atomic
    SET-if-absent, so whichever concurrent caller's `add` actually lands
    is the one that creates the window (and gets 1 back); everyone else
    falls through to `cache.incr`, which is a real atomic INCR once the
    key exists. Used by accounts.magic_links and notifications.sms for
    their own per-account/per-second request counters.

    Args:
        key: Cache key naming this counter's window - the window boundary
            itself (e.g. a per-second or per-hour timestamp) belongs in
            the key; this function only knows how to count.
        window_seconds: TTL to set on a newly created counter.

    Returns:
        The counter's new value after this call's own increment.
    """
    if cache.add(key, 1, timeout=window_seconds):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        # The window expired in the gap between our failed add() and this
        # incr() - vanishingly rare (window_seconds is always measured in
        # whole seconds or more), but not impossible. Treat it as a fresh
        # window rather than letting ValueError escape to the caller.
        cache.add(key, 1, timeout=window_seconds)
        return 1


def check_email_security_settings(*, use_tls: bool, use_ssl: bool) -> None:
    """Rejects EMAIL_USE_TLS and EMAIL_USE_SSL both being True.

    Django's own SMTP backend raises this same contradiction (STARTTLS vs.
    implicit-TLS-from-the-start are mutually exclusive), but only lazily,
    the first time an email actually tries to send. Checked here too, at
    settings-import time, so a bad .env fails loudly at startup instead of
    silently at the first real send - and so this is actually testable
    without needing to reload the whole settings module. Both False is a
    real, working case (plain unencrypted SMTP), not something to guard
    against here.

    Raises:
        ImproperlyConfigured: if both are True.
    """
    if use_tls and use_ssl:
        raise ImproperlyConfigured(
            "EMAIL_USE_TLS and EMAIL_USE_SSL are mutually exclusive - set at most one."
        )
