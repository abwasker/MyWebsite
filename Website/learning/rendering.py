"""Turn a Note's markdown into page-ready HTML.

Wikilink resolution lives here — it is `learning`-specific — while math protection
lives in `blog/markdown_utils.py` behind an opt-in flag. That split is deliberate:
one renderer, no second copy (the lesson recorded in [[Spotify Listening Tracker]]
§3d, where two renderers would have drifted).

Order of operations matters:
  1. substitute `[[wikilinks]]` -> markdown links, using the DB
  2. hand the result to render_markdown_text(protect_math=True)
Math spans are stashed inside step 2, so a wikilink is never substituted inside
one — and in practice nobody writes `[[…]]` inside `$…$`.
"""
from django.urls import reverse

from blog.markdown_utils import render_markdown_text

from .vault import WIKILINK

KIND_URL_NAMES = {
    "module": "learning-module",
    "concept": "learning-concept",
    "source": "learning-source",
}


def note_url(note):
    return reverse(KIND_URL_NAMES[note.kind],
                   kwargs={"path_slug": note.path.slug, "slug": note.slug})


def _escape_markdown_label(text):
    """Stop a title containing ] or ) from breaking out of the link syntax."""
    return text.replace("[", r"\[").replace("]", r"\]").replace("(", r"\(").replace(")", r"\)")


def substitute_wikilinks(text, note):
    """Replace `[[Target]]` / `[[Target|display]]` with markdown links.

    Three outcomes, and the third is the one that matters:
      * resolves to a Note        -> a real link
      * targets the path's MOC    -> a link to the generated path overview
      * resolves to nothing       -> `` `[[Target]]` `` as inline CODE

    The broken case renders as code rather than a link so it is VISIBLY wrong on
    the page. §4.12's existing failure mode is the warning: a missed lookup that
    silently publishes as literal text is a bug nobody notices for months. Code
    styling needs no new sanitiser permissions — `code` is already allowed, where
    a `<span class=…>` would have meant widening ALLOWED_TAGS for every blog post
    too.
    """
    path = note.path
    by_filename = {
        n.vault_filename: n
        for n in path.notes.select_related("path").all()
    }

    def replace(match):
        raw = match.group(1)
        target, _, display = raw.partition("|")
        target = target.split("#")[0].split("^")[0].strip()
        label = _escape_markdown_label((display or target).strip())

        found = by_filename.get(target)
        if found is not None:
            return f"[{label}]({note_url(found)})"
        if path.moc_filename and target == path.moc_filename:
            return f"[{label}]({reverse('learning-path', kwargs={'path_slug': path.slug})})"
        return f"`[[{raw}]]`"

    return WIKILINK.sub(replace, text)


def render_in_note_context(text, note):
    """Render ANY markdown as if it lived in `note`: wikilinks resolved against
    that note's path, math preserved for KaTeX.

    Used for reference bodies AND for the user's own `My Notes`, so a user's
    LaTeX and `[[wikilinks]]` behave identically to reference content — and so
    there is exactly one renderer, per [[Spotify Listening Tracker]] §3d.
    `nh3` inside render_markdown_text is what makes rendering user input safe.
    """
    return render_markdown_text(substitute_wikilinks(text or "", note), protect_math=True)


def render_note(note):
    """Full render for a note's own body."""
    return render_in_note_context(note.body_markdown, note)
