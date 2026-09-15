from notifications.helpers import html_to_plain_text


def test_strips_a_style_blocks_contents_not_just_its_tags():
    # strip_tags on its own only removes tag *markup*, never a tag's own
    # contents - found for real via the sign-in email, but every email
    # extending templates/email/_base.html shares this same <head>
    # <style> block, so this silently affected every other one too.
    html = "<html><head><style>body { color: red; }</style></head><body><p>Hello</p></body></html>"

    assert html_to_plain_text(html) == "Hello"


def test_strips_a_script_blocks_contents_too():
    html = "<html><head><script>alert(1)</script></head><body><p>Hi</p></body></html>"

    assert html_to_plain_text(html) == "Hi"


def test_collapses_multiple_blank_lines_into_one():
    html = "<p>First</p>\n\n\n\n<p>Second</p>"

    assert html_to_plain_text(html) == "First\n\nSecond"


def test_strips_ordinary_tags_and_trims_surrounding_whitespace():
    html = "  <div><p>Hello <strong>world</strong></p></div>  "

    assert html_to_plain_text(html) == "Hello world"


def test_decodes_html_entities_left_behind_by_strip_tags():
    # strip_tags only removes tag markup, never decodes entities - a name
    # or parents_label containing "&"/an apostrophe survives Django's own
    # (correct, expected) autoescaping of the source HTML as "&amp;"/
    # "&#x27;", which strip_tags alone leaves as literal text instead of
    # the real character a plain-text fallback actually needs.
    html = "<p>Shloime &amp; Ruchie&#x27;s Leah</p>"

    assert html_to_plain_text(html) == "Shloime & Ruchie's Leah"


def test_does_not_treat_an_intentionally_typed_escaped_tag_as_real_markup():
    # unescape() must run after strip_tags(), not before - if it ran
    # first, someone's literal, intentionally-typed "&lt;b&gt;" would
    # become "<b>" text that strip_tags would then wrongly remove as if
    # it were real markup.
    html = "<p>Use &lt;b&gt; for bold</p>"

    assert html_to_plain_text(html) == "Use <b> for bold"
