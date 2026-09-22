"""Small, reusable pieces of notification logic that don't belong to any one view or task.

Pulled out once the same code showed up more than once (see AGENTS.md's
helpers.py note).
"""

import html
import re

from django.utils.html import strip_tags

from accounts.models import Account
from family.models import Person, Union
from notifications.audience import preference_status
from notifications.enums import ChannelEnum
from notifications.models import EventType


def html_to_plain_text(html_content: str) -> str:
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

    `html.unescape()` runs after `strip_tags`, never before - strip_tags
    only recognizes real markup, so unescaping first would turn someone's
    literal, intentionally-typed "&lt;b&gt;" into "<b>" text that
    strip_tags would then wrongly treat as a real tag and remove. Running
    it after is safe: only markup has already been stripped by then, so
    whatever's left is exactly the entities (a name's "&amp;", a
    Broadcast author's "&#x27;") that need decoding back to plain
    characters for a fallback that's read as plain text, not HTML - found
    via a family name with an "&" in it rendering as literal "&amp;" in
    the SMS body once the HTML source's own (correct, expected)
    autoescaping was baked in ahead of this step.

    strip_tags() only removes tag *markup* - it has no concept of block
    vs. inline elements, so adjacent block-level content is otherwise
    concatenated with zero separation at all ("systemthis is boldthis
    is a header" instead of three separate lines) - found for real in
    a Broadcast's own rich text (Trix produces <div>/<br>/<h1>/<li>
    for what reads as separate lines to whoever wrote it). A newline is
    inserted wherever a block boundary actually was, before strip_tags
    runs, so those still read as separate lines here - a caller that
    wants them flattened to one line anyway (the SMS budget) still can,
    via `" ".join(text.split())` on this function's own output.
    """
    text = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(div|p|li|h[1-6]|blockquote|pre)>", "\n", text, flags=re.IGNORECASE)
    return html.unescape(re.sub(r"\n\s*\n+", "\n\n", strip_tags(text)).strip())


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
                "label": ChannelEnum(code).label,
                "subscribed": status.subscribed,
                "reason": status.reason,
            }
        )
    return rows
