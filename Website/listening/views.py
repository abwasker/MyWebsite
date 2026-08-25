"""The permission-gated listening table and its downloads (scope Phase 3b / 3d).

Decisions worth knowing before changing anything here:

* **Gated on an explicit permission, ``listening.view_dashboard``** — not on
  ``login_required`` and deliberately no longer on ``is_staff`` either (§4.3,
  and the parent scope's §4.8.1).

  ``is_staff`` only ever meant "may open Django's /admin/". It is an admin-site
  flag, not a trust level. The parent project plans 3-5 blog authors, and the
  moment one is given staff so they can write posts, a staff-gated page would
  hand them this private data in the same act — no code change, no warning.
  Gating on a permission makes admin access and feature access independent: a
  non-staff account can hold this permission, and a staff account without it
  gets nothing.
* **The download is a second door to the same private data**, so it carries the
  same gate, enforced on its own view rather than inherited or assumed.
* ⚠️ **Django's admin is a THIRD door** that this does not close: a staff user
  holding ``listening.view_listeningitem`` can browse the same rows at
  /admin/listening/listeningitem/. Consequence recorded in the parent scope
  §4.8 — the future "Blog Author" group must hold the ``blog.*`` permissions and
  none of the ``listening.*`` ones.
* **The table counts sessions, not observations** (§4 / ``stats.session_counts``).
* **The filter lives in one place** (``export.filtered_items``), used by the page,
  the download and the management command. "The download matches the screen" has
  to mean the same code, or it will drift.
"""

from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from . import export
from .stats import humanize_ms, session_counts, session_gap

PER_PAGE = 25

# One constant, used by both views, so the page and the download can never end up
# checking different things — the same single-source rule export.filtered_items()
# follows. Declared on the ListeningAccess anchor model in models.py.
LISTENING_PERMISSION = "listening.view_dashboard"

# Whitelist of sortable columns. User input is never passed to order_by()
# directly — an unrecognised key falls back to the default rather than reaching
# the ORM, which would otherwise expose arbitrary field names and related-model
# traversal through a query parameter.
SORT_FIELDS = {
    "name": "name",
    "creator": "creator_name",
    "type": "item_type",
    "first": "first_played_at",
    "last": "last_played_at",
    "est": "est_ms",
    # Sessions are computed sequentially in Python, so they cannot be sorted in
    # SQL. Handled separately below.
    "sessions": None,
}
DEFAULT_SORT = "last"

# Content types are explicit about charset: the data contains non-ASCII creator
# names, and a browser guessing the local codepage would mojibake them.
DOWNLOAD_CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
}
DOWNLOAD_EXTENSIONS = {"csv": "csv", "markdown": "md"}


def read_filters(request):
    """The type/search filter as the request asked for it, validated.

    Shared by the page and the download so a download link built from the page's
    query string selects exactly the rows the page was showing.
    """
    item_type = request.GET.get("type", "all")
    if item_type not in export.TYPE_FILTERS:
        item_type = "all"
    return item_type, (request.GET.get("q") or "").strip()


@login_required
@permission_required(LISTENING_PERMISSION, raise_exception=True)
def listening_home(request):
    """The listening table. Requires listening.view_dashboard."""

    sort = request.GET.get("sort", DEFAULT_SORT)
    if sort not in SORT_FIELDS:
        sort = DEFAULT_SORT
    descending = request.GET.get("dir", "desc") != "asc"

    item_type, query = read_filters(request)
    items = export.filtered_items(item_type=item_type, query=query, ordering=None)

    total_matching = items.count()

    if sort == "sessions":
        # Sessions aren't a database column, so the whole filtered set has to be
        # materialised, counted and sorted before it can be paginated.
        #
        # Fine at this scale (tens to low thousands of items). If the library
        # ever grows past that, the fix is to store a session count on
        # ListeningItem and maintain it in the collector, the same way the other
        # rollups already work.
        ordered = list(items)
        counts = session_counts([item.pk for item in ordered])
        ordered.sort(key=lambda item: counts.get(item.pk, 0), reverse=descending)
        page_source = ordered
    else:
        field = SORT_FIELDS[sort]
        page_source = items.order_by(f"-{field}" if descending else field)
        counts = None

    paginator = Paginator(page_source, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    # Only the rows actually being displayed need session counts — unless the
    # sort already forced computing them for everything.
    if counts is None:
        counts = session_counts([item.pk for item in page])

    rows = [
        {
            "item": item,
            "sessions": counts.get(item.pk, 0),
            "estimated": humanize_ms(item.est_ms),
        }
        for item in page
    ]

    # Query string minus `page`, so pagination links keep the current sort,
    # filter and search instead of silently resetting them.
    preserved = request.GET.copy()
    preserved.pop("page", None)

    return render(
        request,
        "listening/listening_home.html",
        {
            "rows": rows,
            "page": page,
            "paginator": paginator,
            "total_matching": total_matching,
            "sort": sort,
            "direction": "desc" if descending else "asc",
            "item_type": item_type,
            "query": query,
            "preserved_query": preserved.urlencode(),
            "session_gap_minutes": int(session_gap().total_seconds() // 60),
        },
    )


@login_required
@permission_required(LISTENING_PERMISSION, raise_exception=True)
def listening_download(request, output_format):
    """Download the current view of the table. Requires listening.view_dashboard.

    Honours the same ``type`` and ``q`` parameters as the page, so a link built
    from the page's own query string yields exactly the rows on screen. A
    download that ignored the active filter would be a trap: you would ask for
    podcasts and silently receive everything.
    """
    if output_format not in export.RENDERERS:
        raise Http404("Unknown export format")

    item_type, query = read_filters(request)
    items = export.filtered_items(item_type=item_type, query=query)
    payload = export.render(items, output_format)

    if output_format == "csv":
        # Same BOM as the file the management command writes: without it Excel
        # assumes the local codepage and mangles the accented creator names.
        payload = "﻿" + payload

    response = HttpResponse(payload, content_type=DOWNLOAD_CONTENT_TYPES[output_format])

    stamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    suffix = "" if item_type == "all" else f"-{item_type}"
    filename = f"listening{suffix}-{stamp}.{DOWNLOAD_EXTENSIONS[output_format]}"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    return response
