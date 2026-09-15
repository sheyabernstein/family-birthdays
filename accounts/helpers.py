"""Assistive email validation for data entry.

See accounts.views.ValidateEmailView, called from
accounts/static/accounts/js/email_input.js on person_form.html and
subscriptions.html. This never blocks the actual save: Account.email/
PersonForm.email are still validated by Django's own EmailField
regardless of what this returns - it only makes the UI push back harder
before someone submits a typo, per "we want to try our best to have
valid emails during data entry".
"""

import difflib
from dataclasses import dataclass, field

import dns.exception
import dns.resolver
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

COMMON_DOMAINS = [
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "aol.com",
    "protonmail.com",
    "live.com",
]

COMMON_TLDS = [".com", ".org", ".net", ".co", ".edu", ".gov"]

# Real MX/A lookups against the public DNS, not a local/mocked resolver -
# capped so a slow or unreachable nameserver can't hang the request this
# runs inside of.
DNS_LOOKUP_TIMEOUT_SECONDS = 3.0


@dataclass
class EmailValidationResult:
    address: str
    valid: bool
    message: str
    suggestions: list[str] = field(default_factory=list)


def _suggest_domain(domain: str) -> str | None:
    if domain in COMMON_DOMAINS:
        return None

    match = difflib.get_close_matches(domain, COMMON_DOMAINS, n=1, cutoff=0.8)
    return match[0] if match and match[0].lower() != domain.lower() else None


def _suggest_tld(domain: str) -> str | None:
    if "." not in domain:
        return None

    base, _, tld = domain.rpartition(".")
    full_tld = f".{tld}"
    if full_tld in COMMON_TLDS:
        return None

    match = difflib.get_close_matches(full_tld, COMMON_TLDS, n=1, cutoff=0.75)
    return f"{base}{match[0]}" if match else None


def _domain_accepts_mail(domain: str) -> bool:
    """Report whether the domain is able to receive mail, failing open.

    True unless the domain is *definitively* unable to receive mail -
    genuinely nonexistent (NXDOMAIN), or an explicit RFC 7505 "null MX"
    (a lone `.` exchange, which is a domain saying outright "I never
    accept email" - example.com is a real, live instance of this, not a
    hypothetical). A lookup that merely *fails* (timeout, no reachable
    nameserver, ...) returns True rather than False - "couldn't check"
    should never manufacture a false rejection out of a transient network
    hiccup, unlike a real NXDOMAIN/null-MX finding.
    """
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=DNS_LOOKUP_TIMEOUT_SECONDS)
    except dns.resolver.NXDOMAIN:
        return False
    except dns.resolver.NoAnswer:
        answers = None
    except dns.exception.DNSException:
        return True
    else:
        if len(answers) == 1 and str(answers[0].exchange) == ".":
            return False
        return True

    # No MX record - a domain can still accept mail at its own address
    # (RFC 5321's implicit MX fallback), so check for an A/AAAA record
    # before concluding it can't receive anything.
    for rdtype in ("A", "AAAA"):
        try:
            dns.resolver.resolve(domain, rdtype, lifetime=DNS_LOOKUP_TIMEOUT_SECONDS)
            return True
        except dns.resolver.NXDOMAIN:
            return False
        except dns.exception.DNSException:
            continue
    return True


def validate_email_address(address: str) -> EmailValidationResult:
    """Validate an email's format via Django's own `EmailValidator`, then add typo/deliverability checks.

    Format validation reuses the same check `forms.EmailField` already
    applies at save time, rather than a second, third-party format
    validator - the only things this adds on top are a domain/TLD typo
    suggestion (stdlib `difflib`, no lookup needed) and a real MX/A-record
    check, neither of which Django's validator attempts.
    """
    try:
        validate_email(address)
    except ValidationError:
        return EmailValidationResult(address=address, valid=False, message="Enter a valid email address.")

    local_part, _, domain = address.rpartition("@")

    # A whole-domain match already implies the better fix - running the
    # TLD check too on the same, uncorrected domain would just add a
    # redundant, worse-quality second suggestion for the same typo (e.g.
    # "gmail.con" matches both "gmail.com" as a whole domain *and* ".co"
    # as a plausible TLD on its own).
    suggestions = []
    if domain_suggestion := _suggest_domain(domain):
        suggestions.append(f"{local_part}@{domain_suggestion}")
    elif tld_suggestion := _suggest_tld(domain):
        suggestions.append(f"{local_part}@{tld_suggestion}")

    if not _domain_accepts_mail(domain):
        return EmailValidationResult(
            address=address,
            valid=False,
            message=f'The domain "{domain}" doesn\'t appear to accept email.',
            suggestions=suggestions,
        )

    return EmailValidationResult(
        address=address, valid=True, message="Address is valid.", suggestions=suggestions
    )
