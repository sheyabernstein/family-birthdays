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
