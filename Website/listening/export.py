"""Rendering the listening table as CSV or Markdown.

Extracted from the management command when the download buttons were added
(Phase 3d), so the command and the web download share **one** implementation.
Two renderers would eventually disagree, and an export that contradicts either
the page or the other export is worse than having only one route.

The same reasoning already applies inside this module: ``sessions`` and
``estimated time`` come from :mod:`listening.stats`, the helpers the page itself
uses, rather than being recalculated here.

Format split, deliberate:

* **CSV is for machines** — ISO-8601 timestamps with an offset, integer
  milliseconds, so a spreadsheet can sort and sum. A human-readable duration
  column rides along because raw milliseconds are unreadable at a glance.
* **Markdown is for reading** — formatted dates, ``~`` on every estimate, and the
  same caveats the page carries, so a pasted table can't be mistaken for
  measured data.
"""

import csv
import io

from django.db.models import Q
from django.utils import timezone

from .models import ListeningItem
from .stats import ESTIMATED_MS, humanize_ms, session_counts, session_gap

CSV_HEADER = [
    "name",
    "creator",
    "type",
    "first_listened",
    "last_listened",
    "listens",
    "estimated_ms",
    "estimated",
]

MARKDOWN_HEADER = [
    "Name",
    "Creator",
    "Type",
    "First listened",
    "Last listened",
    "Listens",
    "Est. time",
]

# Shared by the page, the download and the command, so all three agree on what
# "podcasts only" means.
TYPE_FILTERS = {
    "all": None,
    "track": ListeningItem.ItemType.TRACK,
    "episode": ListeningItem.ItemType.EPISODE,
}

DEFAULT_ORDERING = ("-last_played_at", "name")


def filtered_items(item_type="all", query="", ordering=DEFAULT_ORDERING):
    """The annotated, filtered queryset behind the table.

    One definition of the filter for every consumer. When a download is supposed
    to match what is on screen, "supposed to" has to mean the same code.
    """
    items = ListeningItem.objects.annotate(est_ms=ESTIMATED_MS)

    selected = TYPE_FILTERS.get(item_type)
    if selected:
        items = items.filter(item_type=selected)

    query = (query or "").strip()
    if query:
        items = items.filter(Q(name__icontains=query) | Q(creator_name__icontains=query))

    return items.order_by(*ordering) if ordering else items


def escape_markdown_cell(value):
    """Make a value safe inside a Markdown table cell.

    An unescaped ``|`` ends the cell and silently shifts every column after it.
    Not hypothetical: the first real episode collected is titled "Evolution on
    Trial | @MadebyJimbob Vs @PolymathWorld". Newlines would break the row
    outright, so they collapse to spaces.
    """
    text = str(value or "")
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return " ".join(text.split())


def isoformat(value):
    """ISO-8601 in the project's timezone, offset included.

    Rendered through ``localtime`` so CSV shows the same wall clock as the
    Markdown export and the page; the trailing offset (``-05:00`` in Chicago,
    ``+00:00`` in UTC) keeps it unambiguous either way. Storage is always UTC
    regardless — only presentation follows ``TIME_ZONE``.
    """
    return timezone.localtime(value).isoformat() if value else ""


def readable(value):
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else "—"


def render_csv(items, counts):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)

    for item in items:
        writer.writerow([
            item.name,
            item.creator_name,
            item.get_item_type_display(),
            isoformat(item.first_played_at),
            isoformat(item.last_played_at),
            counts.get(item.pk, 0),
            item.est_ms if item.est_ms is not None else "",
            humanize_ms(item.est_ms),
        ])

    return buffer.getvalue()


def render_markdown(items, counts):
    lines = [
        "| " + " | ".join(MARKDOWN_HEADER) + " |",
        "| " + " | ".join(["---"] * len(MARKDOWN_HEADER)) + " |",
    ]

    for item in items:
        estimated = humanize_ms(item.est_ms)
        lines.append("| " + " | ".join([
            escape_markdown_cell(item.name),
            escape_markdown_cell(item.creator_name) or "—",
            escape_markdown_cell(item.get_item_type_display()),
            readable(item.first_played_at),
            readable(item.last_played_at),
            str(counts.get(item.pk, 0)),
            f"~{estimated}" if estimated else "—",
        ]) + " |")

    gap_minutes = int(session_gap().total_seconds() // 60)
    lines += [
        "",
        f"_{len(items)} item(s). Times are {timezone.get_current_timezone_name()}._",
        "",
        "_**Est. time is an estimate, not a measurement.** Spotify publishes no "
        "played-duration, so podcast time is the furthest position observed in the "
        "episode and song time is play count multiplied by track length._",
        "",
        f"_**Listens are sessions, not polls.** Observations more than {gap_minutes} "
        "minutes apart count as separate listens._",
    ]

    return "\n".join(lines) + "\n"


RENDERERS = {
    "csv": render_csv,
    "markdown": render_markdown,
}


def render(items, output_format):
    """Render an already-ordered list of items in the requested format."""
    items = list(items)
    counts = session_counts([item.pk for item in items])
    return RENDERERS[output_format](items, counts)
