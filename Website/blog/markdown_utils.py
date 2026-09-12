import re

import markdown
import nh3
from django.utils.html import escape as html_escape
from django.utils.safestring import mark_safe


OBSIDIAN_IMAGE_PATTERN = re.compile(r"!\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

# Obsidian's rule for the pipe value, verified in the vault 2026-09-11: a
# NUMERIC value is a size, anything else is alt text. Disambiguated by shape,
# so one slot carries both meanings exactly as it does in Obsidian. See §4.12.1.
#
# [0-9] and NOT \d: Python's \d matches Unicode digits, so "٣٠٠" (Arabic-Indic)
# would pass and emit width="٣٠٠" — invalid HTML that browsers ignore silently.
# Leading digit 1-9: "0" is not a size, it is an invisible image.
SIZE_HINT = re.compile(r"^([1-9][0-9]*)(?:x([1-9][0-9]*))?$")

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
    # `width`/`height` carry Obsidian's `|300` size hint (§4.12.1). Integers
    # only — they are emitted solely when SIZE_HINT matched, so no untrusted
    # value reaches the attribute. `style` stays out: not mainly a script
    # vector, but unbounded, and a full-viewport overlay needs no JavaScript.
    # `class` is reserved for alignment, which is parked.
    "img": {"alt", "src", "title", "width", "height"},
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
        pipe_value = (match.group(2) or "").strip()
        content_image = image_lookup.get(reference_name)
        if content_image is None:
            # A miss stays literal on purpose: raising here would 500 a
            # published page over a typo. The admin warns at save time instead.
            return match.group(0)

        size = SIZE_HINT.match(pipe_value) if pipe_value else None
        alt = ("" if size else pipe_value) or content_image.alt_text or reference_name

        # Emit the <img> directly rather than `![alt](url)`, because Markdown's
        # image syntax cannot carry width/height. One path for both sized and
        # unsized embeds, so there is only one behaviour to reason about.
        #
        # The cost: escaping moves from Markdown to us, and getting it wrong is
        # silent. html_escape on the alt is what closes that; nh3's allowlist is
        # the second line, not the first.
        #
        # (A side benefit, not a fix: Markdown's `](url)` breaks on an
        # UNBALANCED ")" in a path — verified. Unreachable here, since Django's
        # get_valid_name strips ")" and spaces at upload time.)
        attrs = [
            f'src="{html_escape(content_image.image.url)}"',
            f'alt="{html_escape(alt)}"',
        ]
        if size:
            attrs.append(f'width="{size.group(1)}"')
            if size.group(2):
                # CSS `height: auto` wins over this presentational hint, so it
                # acts as an aspect-ratio hint (no layout shift) rather than a
                # hard height. Deliberate: a fixed height would stretch the
                # image once max-width kicks in on a narrow screen.
                attrs.append(f'height="{size.group(2)}"')

        return "<img {}>".format(" ".join(attrs))

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


def missing_embed_references(source, owner):
    """Reference names in `source` that no usable ContentImage satisfies.

    Deliberately reuses OBSIDIAN_IMAGE_PATTERN and _content_image_lookup rather
    than re-deriving either, so this can never disagree with what actually
    renders. A name reported here is exactly a name that `replace` above would
    leave as literal text.

    Note a row whose `image` file is empty is a miss too: the lookup skips it,
    so the embed would publish literally even though the row exists.

    Order-preserving and de-duplicated, so the admin warning reads in the order
    the author wrote them and never repeats a name.
    """
    if not source:
        return []

    lookup = _content_image_lookup(owner)
    missing = []
    for match in OBSIDIAN_IMAGE_PATTERN.finditer(source):
        name = match.group(1).strip()
        if name not in lookup and name not in missing:
            missing.append(name)
    return missing
