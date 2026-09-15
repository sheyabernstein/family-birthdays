from django import template
from django.forms import BoundField
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()


@register.simple_tag
def field_label(field: BoundField) -> SafeString:
    """A drop-in replacement for `{{ field.label_tag }}` that marks required fields.

    Appends a required-marker span when the field is required - none of
    this app's hand-written form templates render fields through Django's
    own `{{ form }}` auto-rendering (which would apply `required_css_class`
    for free), so every one of them calls this instead wherever a field
    might be required, to keep that distinction visible to a sighted user
    before they try to submit. Django's own `required` HTML attribute
    already covers native browser validation and screen readers
    regardless of this - the marker is a visual aid on top of that, not a
    new source of truth. Builds the marker into label_tag's own
    `contents` argument (not appended after the rendered tag) so it ends
    up inside the <label>, before the label_suffix, exactly where
    label_tag would put the label text itself.
    """
    if not field.field.required:
        return field.label_tag()
    contents = format_html('<span class="required-marker" aria-hidden="true">*</span> {}', field.label)
    return field.label_tag(contents=contents)
