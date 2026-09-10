import re

import markdown
import nh3
from django.utils.html import escape as html_escape
from django.utils.safestring import mark_safe


OBSIDIAN_IMAGE_PATTERN = re.compile(r"!\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"alt", "src", "title"},
    "th": {"align"},
    "td": {"align"},
}


def render_markdown_text(source, owner=None, protect_math=False):
    """Render markdown to sanitised HTML.

    `protect_math=True` stashes every `$…$` / `$$…$$` span before Markdown runs
    and restores it afterwards, so LaTeX reaches the browser byte-for-byte for
    client-side KaTeX. OFF BY DEFAULT so blog rendering is unchanged — the
    `learning` app opts in. See _protect_math for why this is necessary.
    """
    normalized = normalize_obsidian_embeds(source or "", owner)

    math_spans = []
    if protect_math:
        normalized, math_spans = _protect_math(normalized)

    raw_html = markdown.markdown(
        normalized,
        extensions=["extra", "sane_lists"],
        output_format="html",
    )
    clean_html = nh3.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
    )
    if protect_math:
        clean_html = _restore_math(clean_html, math_spans)
    return mark_safe(clean_html)


# Display math first so `$$…$$` is never mistaken for two empty inline spans.
MATH_SPAN = re.compile(r"\$\$.+?\$\$|(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)", re.S)

# Alphanumeric on purpose: it must survive BOTH Markdown and nh3 untouched.
# Anything with punctuation risks being escaped, linkified or stripped.
_MATH_TOKEN = "zzmathspanzz{}zz"
_MATH_TOKEN_RE = re.compile(r"zzmathspanzz(\d+)zz")


def _protect_math(source):
    """Replace math spans with opaque tokens. Returns (text, spans).

    WHY THIS IS NEEDED (measured, not theoretical — see the Fiber Bundles scope §2.2):
    Markdown mangles LaTeX before it ever reaches the browser.
      * `$\\{x \\in M\\}$` -> `${x \\in M}$`  — backslashes eaten, so the braces
        become KaTeX *grouping* and vanish. Present in 10 spans of the real corpus.
      * `a &= b \\\\ c` -> `a &= b \\ c`      — row breaks in aligned environments destroyed.
    Escaping-based workarounds are whack-a-mole; stashing the spans fixes the
    whole class, including cases not yet met.
    """
    spans = []

    def stash(match):
        spans.append(match.group(0))
        return _MATH_TOKEN.format(len(spans) - 1)

    return MATH_SPAN.sub(stash, source), spans


def _restore_math(html, spans):
    """Put the math back, HTML-escaped.

    Escaping matters: the restored text bypasses nh3, so `<`, `>` and `&` must not
    be able to introduce markup. KaTeX reads `textContent`, where `&lt;` is `<`
    again — so escaping costs nothing and closes the hole.
    """
    def pop(match):
        index = int(match.group(1))
        return html_escape(spans[index]) if index < len(spans) else match.group(0)

    return _MATH_TOKEN_RE.sub(pop, html)


def normalize_obsidian_embeds(source, owner=None):
    if owner is None:
        return source

    image_lookup = _content_image_lookup(owner)

    def replace(match):
        reference_name = match.group(1).strip()
        alt_text = (match.group(2) or "").strip()
        content_image = image_lookup.get(reference_name)
        if content_image is None:
            return match.group(0)

        alt = alt_text or content_image.alt_text or reference_name
        return f"![{alt}]({content_image.image.url})"

    return OBSIDIAN_IMAGE_PATTERN.sub(replace, source)


def _content_image_lookup(owner):
    manager = getattr(owner, "content_images", None)
    if manager is None:
        return {}

    return {
        image.reference_name: image
        for image in manager.all()
        if image.image
    }
