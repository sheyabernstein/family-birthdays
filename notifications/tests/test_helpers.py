import pytest

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


def test_inserts_a_line_break_across_adjacent_block_level_tags():
    # strip_tags() alone has no concept of block vs. inline elements -
    # adjacent block-level content with no whitespace between the tags
    # in the source (Trix's own real output) used to be concatenated
    # into one word with zero separation at all.
    html = "<div>First line<br><strong>second line</strong></div><h1>third line</h1>"

    assert html_to_plain_text(html) == "First line\nsecond line\nthird line"


@pytest.mark.parametrize(
    ["html", "expected"],
    [
        ["<div>First</div><div>Second</div>", "First\nSecond"],
        ["<p>First</p><p>Second</p>", "First\nSecond"],
        ["First<br>Second", "First\nSecond"],
        ["<ul><li>First</li><li>Second</li></ul>", "- First\n- Second"],
        ["<ol><li>First</li><li>Second</li></ol>", "1. First\n2. Second"],
        # <ul>/<ol> themselves are never converted to a line break (only
        # <li> is) - these two rely on each </li>'s own trailing break
        # already sitting right up against the list's own closing tag,
        # which strip_tags then removes with no text of its own to
        # separate - not a special case in the implementation, but worth
        # locking in given the wrapper tag itself gets no treatment.
        ["<p>Intro</p><ul><li>Only item</li></ul><p>Outro</p>", "Intro\n- Only item\nOutro"],
        # Numbering restarts at 1 for each separate <ol> - not a single
        # counter running across the whole document.
        ["<ol><li>A</li></ol><ol><li>B</li></ol>", "1. A\n1. B"],
        ["<h1>First</h1><h6>Second</h6>", "First\nSecond"],
        ["<blockquote>First</blockquote><pre>Second</pre>", "First\nSecond"],
        # Inline tags never get their own line break - only the block
        # tags in BROADCAST_ALLOWED_TAGS (see notifications/models.py)
        # plus the wider h1-h6 range do.
        ["<strong>First</strong> <em>Second</em>", "First Second"],
        ["<a href='https://example.com'>First</a> Second", "First Second"],
    ],
    ids=[
        "adjacent divs",
        "adjacent paragraphs",
        "a line break tag",
        "an unordered list gets bullet markers",
        "an ordered list gets numbered markers",
        "a bulleted list boundary against surrounding content",
        "numbering restarts for each separate ordered list",
        "headings from h1 to h6",
        "a blockquote followed by a pre block",
        "inline formatting tags stay on one line",
        "a link stays on one line",
    ],
)
def test_html_to_plain_text_with_various_markup(html, expected):
    assert html_to_plain_text(html) == expected


def test_does_not_treat_an_intentionally_typed_escaped_tag_as_real_markup():
    # unescape() must run after strip_tags(), not before - if it ran
    # first, someone's literal, intentionally-typed "&lt;b&gt;" would
    # become "<b>" text that strip_tags would then wrongly remove as if
    # it were real markup.
    html = "<p>Use &lt;b&gt; for bold</p>"

    assert html_to_plain_text(html) == "Use <b> for bold"
