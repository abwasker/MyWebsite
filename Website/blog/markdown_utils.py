import re

import markdown
import nh3
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


def render_markdown_text(source, owner=None):
    normalized = normalize_obsidian_embeds(source or "", owner)
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
    return mark_safe(clean_html)


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
