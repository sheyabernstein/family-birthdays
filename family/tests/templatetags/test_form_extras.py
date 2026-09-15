from django import forms
from django.template import Context, Template

from family.templatetags.form_extras import field_label


class _SampleForm(forms.Form):
    required_field = forms.CharField()
    optional_field = forms.CharField(required=False)


def test_field_label_adds_a_marker_for_a_required_field():
    form = _SampleForm()

    result = field_label(form["required_field"])

    assert 'class="required-marker"' in result
    assert "Required field" in result
    assert result.index("required-marker") < result.index("Required field")


def test_field_label_omits_the_marker_for_an_optional_field():
    form = _SampleForm()

    result = field_label(form["optional_field"])

    assert "required-marker" not in result
    assert result == form["optional_field"].label_tag()


def test_field_label_still_produces_a_real_label_element_pointing_at_the_input():
    form = _SampleForm()

    result = field_label(form["required_field"])

    assert f'for="{form["required_field"].id_for_label}"' in result


def test_field_label_renders_correctly_from_a_template():
    template = Template("{% load form_extras %}{% field_label form.required_field %}")
    form = _SampleForm()

    rendered = template.render(Context({"form": form}))

    assert "required-marker" in rendered
