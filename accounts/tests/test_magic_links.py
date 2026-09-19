import uuid

from accounts import magic_links


def test_issued_token_can_be_consumed_once():
    account_uuid = str(uuid.uuid4())
    token = magic_links.issue_token(account_uuid=account_uuid, channel="email", destination="a@example.com")

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
