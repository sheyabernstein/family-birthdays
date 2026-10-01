import uuid

from django.core.cache import cache

from accounts import magic_links


def test_issued_token_can_be_consumed_once():
    account_uuid = str(uuid.uuid4())
    token, _code = magic_links.issue_token(
        account_uuid=account_uuid, channel="email", destination="a@example.com"
    )

    payload = magic_links.consume_token(token)
    assert payload == {"account_uuid": account_uuid, "channel": "email", "destination": "a@example.com"}

    assert magic_links.consume_token(token) is None


def test_unknown_token_is_not_valid():
    assert magic_links.consume_token("does-not-exist") is None


def test_rate_limit_allows_up_to_the_max_then_blocks():
    # A fresh id each run - the rate-limit counter lives in Redis with a
    # real TTL, not a DB transaction pytest can roll back between tests.
    account_uuid = str(uuid.uuid4())

    results = [
        magic_links.is_rate_limited(account_uuid) for _ in range(magic_links.RATE_LIMIT_MAX_PER_WINDOW)
    ]
    assert all(blocked is False for blocked in results)

    assert magic_links.is_rate_limited(account_uuid) is True


def test_issued_code_can_be_consumed_once():
    account_uuid = str(uuid.uuid4())
    _token, code = magic_links.issue_token(
        account_uuid=account_uuid, channel="email", destination="a@example.com"
    )

    payload, reason = magic_links.consume_code(account_uuid=account_uuid, code=code)
    assert payload == {"account_uuid": account_uuid, "channel": "email", "destination": "a@example.com"}
    assert reason is None

    # The code's own cache entry outlives the token it points at - only
    # consume_token's GETDEL removed that, so this second call still
    # finds the code, just pointing at a now-gone token.
    payload, reason = magic_links.consume_code(account_uuid=account_uuid, code=code)
    assert payload is None
    assert reason == magic_links.CodeVerifyFailureReason.TOKEN_EXPIRED


def test_code_is_case_insensitive():
    account_uuid = str(uuid.uuid4())
    _token, code = magic_links.issue_token(
        account_uuid=account_uuid, channel="email", destination="a@example.com"
    )

    payload, _reason = magic_links.consume_code(account_uuid=account_uuid, code=code.lower())

    assert payload is not None


def test_consuming_the_code_also_burns_the_link_token():
    # Two pointers at the same session - redeeming either one invalidates
    # the other, never a double sign-in.
    account_uuid = str(uuid.uuid4())
    token, code = magic_links.issue_token(
        account_uuid=account_uuid, channel="email", destination="a@example.com"
    )

    magic_links.consume_code(account_uuid=account_uuid, code=code)

    assert magic_links.consume_token(token) is None


def test_consuming_the_link_token_leaves_the_code_unable_to_sign_in_again():
    account_uuid = str(uuid.uuid4())
    token, code = magic_links.issue_token(
        account_uuid=account_uuid, channel="email", destination="a@example.com"
    )

    magic_links.consume_token(token)

    payload, reason = magic_links.consume_code(account_uuid=account_uuid, code=code)
    assert payload is None
    assert reason == magic_links.CodeVerifyFailureReason.TOKEN_EXPIRED


def test_code_rejects_the_wrong_account():
    # A code that resolves to a real token, just for a different account
    # than the caller claimed - treated as a plain wrong code, not a hint.
    real_account_uuid = str(uuid.uuid4())
    wrong_account_uuid = str(uuid.uuid4())
    _token, code = magic_links.issue_token(
        account_uuid=real_account_uuid, channel="email", destination="a@example.com"
    )

    payload, reason = magic_links.consume_code(account_uuid=wrong_account_uuid, code=code)
    assert payload is None
    assert reason == magic_links.CodeVerifyFailureReason.ACCOUNT_MISMATCH
    # And the real account can still redeem it - the mismatched attempt
    # above didn't burn it.
    payload, _reason = magic_links.consume_code(account_uuid=real_account_uuid, code=code)
    assert payload is not None


def test_unknown_code_is_not_valid():
    payload, reason = magic_links.consume_code(account_uuid=str(uuid.uuid4()), code="ZZZZZZ")
    assert payload is None
    assert reason == magic_links.CodeVerifyFailureReason.UNKNOWN_CODE


def test_code_guessing_locks_out_after_too_many_wrong_attempts_on_one_code():
    # A separate account_uuid for this first loop - is_code_guess_blocked
    # counts every call against the account regardless of which code was
    # tried, so sharing one account across both loops would trip the
    # account-wide lockout first and mask the per-code one this test is
    # actually about.
    warmup_account_uuid = str(uuid.uuid4())
    magic_links.issue_token(account_uuid=warmup_account_uuid, channel="email", destination="a@example.com")
    for _ in range(magic_links.CODE_MAX_ATTEMPTS_PER_CODE):
        payload, _reason = magic_links.consume_code(account_uuid=warmup_account_uuid, code="WRONG1")
        assert payload is None

    # The real code, tried right after - already locked out for this
    # specific wrong code, regardless of whether the real one would work.
    account_uuid = str(uuid.uuid4())
    _token, code = magic_links.issue_token(
        account_uuid=account_uuid, channel="email", destination="a@example.com"
    )
    for _ in range(magic_links.CODE_MAX_ATTEMPTS_PER_CODE):
        magic_links.consume_code(account_uuid=account_uuid, code="WRONG2")
    payload, reason = magic_links.consume_code(account_uuid=account_uuid, code="WRONG2")
    assert payload is None
    assert reason == magic_links.CodeVerifyFailureReason.CODE_ATTEMPTS_EXCEEDED


def test_issue_unique_code_retries_on_a_collision(monkeypatch):
    # Force the very first roll to land on a code that's already reserved
    # by someone else's still-pending sign-in.
    cache.set(magic_links._code_key("AAAAAA"), "other-token", timeout=60)
    rolls = iter(["A"] * magic_links.CODE_LENGTH + list("BBBBBB"))
    monkeypatch.setattr(magic_links.secrets, "choice", lambda alphabet: next(rolls))

    code = magic_links._issue_unique_code("real-token")

    assert code == "BBBBBB"
    # The collided-with code's own entry is untouched - never silently
    # repointed at this token instead of the one it actually belongs to.
    assert cache.get(magic_links._code_key("AAAAAA")) == "other-token"


def test_issue_unique_code_falls_back_to_overwrite_after_exhausting_retries(monkeypatch):
    cache.set(magic_links._code_key("AAAAAA"), "other-token", timeout=60)
    monkeypatch.setattr(magic_links.secrets, "choice", lambda alphabet: "A")

    code = magic_links._issue_unique_code("real-token")

    assert code == "AAAAAA"
    assert cache.get(magic_links._code_key("AAAAAA")) == "real-token"


def test_code_guessing_locks_out_the_whole_account_after_too_many_attempts():
    # Independent of any one code's own attempt cap - spreading guesses
    # across many different wrong codes for the same account is still
    # capped, tighter than is_rate_limited's own per-hour request limit.
    account_uuid = str(uuid.uuid4())

    for i in range(magic_links.CODE_MAX_ATTEMPTS_PER_ACCOUNT):
        magic_links.consume_code(account_uuid=account_uuid, code=f"GUESS{i}")

    _token, code = magic_links.issue_token(
        account_uuid=account_uuid, channel="email", destination="a@example.com"
    )
    payload, reason = magic_links.consume_code(account_uuid=account_uuid, code=code)
    assert payload is None
    assert reason == magic_links.CodeVerifyFailureReason.ACCOUNT_LOCKED
