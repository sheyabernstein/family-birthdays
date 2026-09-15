"""Small, reusable pieces of notification logic that don't belong to any one view or task.

Pulled out once the same code showed up more than once (see AGENTS.md's
helpers.py note).
"""

import re

from django.utils.html import strip_tags

from accounts.models import Account
from family.models import Person, Union
from notifications.audience import preference_status
from notifications.enums import ChannelEnum
from notifications.models import EventType


def html_to_plain_text(html: str) -> str:
    """Collapses rendered HTML down to a readable plain-text fallback.

    Shared by the email/SMS-adjacent body derivation in
    notifications.tasks._render_occurrence_message and
    _render_broadcast_message, both of which need the exact same
    strip-tags-then-collapse-blank-lines treatment.

    `<style>`/`<script>` blocks are cut out before `strip_tags` runs -
    that function only ever removes tag *markup*, never a tag's own
    contents (this is documented Django behavior, not a bug in it), so
    every email's `<head><style>...</style></head>` - shared by every
    template extending templates/email/_base.html - would otherwise leak
    its raw CSS text into the plain-text fallback verbatim. Found for
    real via the sign-in email, but it silently affected every other
    occurrence/broadcast email's plain-text body too, all the way back
    to the HTML template rewrite.
    """
    html = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"\n\s*\n+", "\n\n", strip_tags(html)).strip()


def channel_rows(
    account: Account,
    event_type: EventType,
    channels: list[tuple[str, str]],
    *,
    person: Person | None = None,
    union: Union | None = None,
) -> list[dict]:
    """Builds one row per channel describing an account's subscription status.

    Shared by family.views.PersonDetailView's own-person and own-union
    event rows, which otherwise built this exact dict twice.

    Args:
        account: Whose subscription status to resolve.
        event_type: The event type to check.
        channels: (code, destination) pairs to build a row for.
        person: The subject, if this is a person-scoped check.
        union: The subject, if this is a union-scoped check.

    Returns:
        One dict per channel: code, label, whether currently subscribed,
        and the reason for that state.
    """
    rows = []
    for code, _destination in channels:
        status = preference_status(account, event_type, person=person, union=union, channel=code)
        rows.append(
            {
                "code": code,
                "label": ChannelEnum[code],
                "subscribed": status.subscribed,
                "reason": status.reason,
            }
        )
    return rows
