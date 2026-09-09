from bot.telegram.markdown import md_to_html


def test_escapes_html_first():
    assert md_to_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_fenced_code_block_with_language_tag():
    text = "before\n```python\nprint('hi')\n```\nafter"
    assert md_to_html(text) == "before\n<pre>print('hi')</pre>\nafter"


def test_fenced_code_block_without_language_tag():
    text = "```\nplain code\n```"
    assert md_to_html(text) == "<pre>plain code</pre>"


def test_inline_code():
    assert md_to_html("run `ls -la` now") == "run <code>ls -la</code> now"


def test_bold_star_and_underscore():
    assert md_to_html("**bold**") == "<b>bold</b>"
    assert md_to_html("__bold__") == "<b>bold</b>"


def test_italic_star_and_underscore():
    assert md_to_html("*italic*") == "<i>italic</i>"
    assert md_to_html("_italic_") == "<i>italic</i>"


def test_italic_star_survives_math_and_bullets():
    assert md_to_html("2 * 3") == "2 * 3"
    assert md_to_html("* bullet item") == "• bullet item"


def test_strikethrough():
    assert md_to_html("~~gone~~") == "<s>gone</s>"


def test_headings():
    assert md_to_html("# Title") == "<b>Title</b>"
    assert md_to_html("## Sub") == "<b>Sub</b>"
    assert md_to_html("### Sub sub") == "<b>Sub sub</b>"


def test_bullets_keep_indentation_numbered_lists_unchanged():
    text = "* one\n  - two\n+ three\n1. first\n2. second"
    assert md_to_html(text) == "• one\n  • two\n• three\n1. first\n2. second"


def test_markdown_link():
    assert md_to_html("[docs](https://example.com)") == '<a href="https://example.com">docs</a>'


def test_collapses_triple_newlines():
    assert md_to_html("a\n\n\n\nb") == "a\n\nb"


def test_code_content_not_touched_by_other_rules():
    text = "`**not bold**`\n```\n**not bold either**\n```"
    assert md_to_html(text) == "<code>**not bold**</code>\n<pre>**not bold either**</pre>"


def test_literal_html_in_model_text_stays_escaped():
    assert md_to_html("Watch out <b>now</b>.") == "Watch out &lt;b&gt;now&lt;/b&gt;."


def test_real_sample():
    text = (
        "Based on the label visible at the bottom of the device, this is a "
        "**TP-Link** network switch.\n\n"
        "Here is a breakdown of what you are looking at:\n\n"
        '*   **It is an Unmanaged PoE Switch:** This is indicated by the "PoE Status" lights.\n'
        "*   **Port 5 (Orange Light):** The orange light indicates **10/100 Mbps** speed & PoE.\n\n"
        "**Summary:** You have a TP-Link PoE switch <3."
    )
    expected = (
        "Based on the label visible at the bottom of the device, this is a "
        "<b>TP-Link</b> network switch.\n\n"
        "Here is a breakdown of what you are looking at:\n\n"
        '• <b>It is an Unmanaged PoE Switch:</b> This is indicated by the "PoE Status" lights.\n'
        "• <b>Port 5 (Orange Light):</b> The orange light indicates <b>10/100 Mbps</b> speed &amp; PoE.\n\n"
        "<b>Summary:</b> You have a TP-Link PoE switch &lt;3."
    )
    result = md_to_html(text)
    assert result == expected
    assert "<b>TP-Link</b>" in result
    assert '• <b>It is an Unmanaged PoE Switch:</b>' in result
