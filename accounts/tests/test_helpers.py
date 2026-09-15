import dns.exception
import dns.resolver
import pytest

from accounts.helpers import validate_email_address


class _FakeMXRecord:
    def __init__(self, exchange):
        self.exchange = exchange


@pytest.mark.parametrize(
    ["address", "expected_message"],
    [
        ["not-an-email", "Enter a valid email address."],
        ["missing-at-sign.example.com", "Enter a valid email address."],
        ["", "Enter a valid email address."],
    ],
    ids=[
        "no at sign at all",
        "no at sign, just a domain-looking string",
        "blank address",
    ],
)
def test_format_validation_rejects_before_touching_dns(monkeypatch, address, expected_message):
    def _unexpected_call(*args, **kwargs):
        raise AssertionError("DNS should never be queried for a format-invalid address")

    monkeypatch.setattr("dns.resolver.resolve", _unexpected_call)

    result = validate_email_address(address)

    assert result.valid is False
    assert result.message == expected_message
    assert result.suggestions == []


@pytest.mark.parametrize(
    ["address", "expected_suggestions"],
    [
        ["someone@gmial.com", ["someone@gmail.com"]],
        ["someone@gmail.con", ["someone@gmail.com"]],
        ["someone@gmail.com", []],
        ["someone@my-own-family-domain.example", []],
    ],
    ids=[
        "a misspelled common domain is suggested",
        "a misspelled common tld is suggested",
        "an already-correct common domain gets no suggestion",
        "an unrecognized but not close domain gets no suggestion",
    ],
)
def test_domain_and_tld_typo_suggestions(monkeypatch, address, expected_suggestions):
    monkeypatch.setattr("accounts.helpers._domain_accepts_mail", lambda domain: True)

    result = validate_email_address(address)

    assert result.valid is True
    assert result.suggestions == expected_suggestions


@pytest.mark.parametrize(
    ["resolve_behavior", "expected_valid"],
    [
        [lambda domain, rdtype, lifetime: [_FakeMXRecord("mail.example.com.")], True],
        [
            lambda domain, rdtype, lifetime: (_ for _ in ()).throw(dns.resolver.NXDOMAIN()),
            False,
        ],
        [lambda domain, rdtype, lifetime: [_FakeMXRecord(".")], False],
        [
            lambda domain, rdtype, lifetime: (_ for _ in ()).throw(dns.exception.Timeout()),
            True,
        ],
    ],
    ids=[
        "a real mx record is valid",
        "a genuinely nonexistent domain is invalid",
        "an rfc 7505 null mx is invalid",
        "a dns lookup that times out is treated as valid - can't tell isn't invalid",
    ],
)
def test_mx_lookup_outcomes(monkeypatch, resolve_behavior, expected_valid):
    monkeypatch.setattr("dns.resolver.resolve", resolve_behavior)

    result = validate_email_address("someone@example.com")

    assert result.valid is expected_valid


def test_a_domain_with_no_mx_but_a_real_a_record_is_valid(monkeypatch):
    def fake_resolve(domain, rdtype, lifetime):
        if rdtype == "MX":
            raise dns.resolver.NoAnswer()
        return ["1.2.3.4"]

    monkeypatch.setattr("dns.resolver.resolve", fake_resolve)

    result = validate_email_address("someone@example.com")

    assert result.valid is True


def test_a_domain_with_no_mx_and_no_a_record_is_invalid(monkeypatch):
    def fake_resolve(domain, rdtype, lifetime):
        if rdtype == "MX":
            raise dns.resolver.NoAnswer()
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr("dns.resolver.resolve", fake_resolve)

    result = validate_email_address("someone@example.com")

    assert result.valid is False
    assert "doesn't appear to accept email" in result.message
