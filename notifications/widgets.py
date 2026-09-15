"""The Broadcast rich-text editor widget.

Split out into its own module for the same reason family/widgets.py
exists: this is a self-contained rendering concern, not form validation
or provisioning logic.
"""

from django import forms
from django.forms.renderers import BaseRenderer
from django.utils.html import format_html


class TrixEditorWidget(forms.Widget):
    """Renders Trix's own two-element pattern for a rich-text field.

    A hidden <input> holds the actual HTML value, plus a <trix-editor> custom element (CDN
    script/CSS in broadcast_list.html/broadcast_form.html, the same
    small-CDN-library approach as Tom Select) bound to it via the
    `input` attribute - instead of a plain <textarea>. Trix reads the
    hidden input's initial `value` to prefill the editor on load, and
    keeps that value in sync on every edit; there's no separate submit-
    time sync step needed, and the field still round-trips through
    Django's normal form machinery exactly like any other CharField.

    Not a HiddenInput subclass on purpose - this is a big, visible
    editor, not a hidden field, and inheriting HiddenInput would mark it
    as one for form.hidden_fields()/is_hidden purposes.

    Broadcast.save() sanitizes whatever HTML ends up here (see
    notifications.models.Broadcast) - this widget only controls how the
    value is authored, not what's ultimately trusted to render in an
    email.
    """

    def render(
        self,
        name: str,
        value: str | None,
        attrs: dict | None = None,
        renderer: BaseRenderer | None = None,
    ) -> str:
        attrs = attrs or {}
        widget_id = attrs.get("id") or f"id_{name}"
        return format_html(
            '<input type="hidden" id="{}" name="{}" value="{}">\n<trix-editor input="{}"></trix-editor>',
            widget_id,
            name,
            value or "",
            widget_id,
        )
