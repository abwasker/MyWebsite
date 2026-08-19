"""Derived numbers for display — sessions, estimated time, and formatting.

Kept out of ``models.py`` because none of this is stored: it is all computed at
read time from the append-only event log.

The important one is :func:`session_counts`. See the scope's §4 note: for polled
episodes, consecutive polls during a single long listen each write their own
event, so ``ListeningItem.observation_count`` is an *observation* count, not a
play count. A 3-hour episode polled every 5 minutes yields ~36 observations and
**one** listen. Showing the raw number would make the table read as nonsense for
exactly the long-form podcasts this project exists to track, so the page groups
events into sessions instead.
"""

from django.conf import settings
from django.db.models import BigIntegerField, Case, F, When
from django.utils import timezone

from .models import ListeningItem, PlayEvent

# A gap longer than this starts a new listening session.
#
# The collector's target poll interval is 5 minutes (§4.5), so a single
# continuous listen produces observations roughly 5 minutes apart. The threshold
# has to sit ABOVE that or ordinary jitter — a poll firing at 5m01s, a timer
# hiccup, a sleeping phone — would split one listen into several. 15 minutes
# absorbs two consecutive missed polls.
#
# The error is deliberately biased: too large merges two genuinely separate
# listens into one, too small inflates the count. Undercounting is the less
# misleading failure, since the whole point of this column is to stop the table
# claiming 36 plays of one episode.
#
# Overridable so the Phase 4 soak can tune it without a code change.
DEFAULT_SESSION_GAP_MINUTES = 15


def session_gap():
    """The configured session-splitting gap, as a timedelta."""
    minutes = getattr(settings, "LISTENING_SESSION_GAP_MINUTES", DEFAULT_SESSION_GAP_MINUTES)
    return timezone.timedelta(minutes=minutes)


def session_counts(item_ids):
    """Map ``{item_id: session_count}`` for the given items.

    One query for every event belonging to those items, grouped in Python — the
    gap logic is sequential and not portably expressible in SQL across both
    SQLite and MySQL, which §4 requires.

    Items with no events are absent from the result; callers should treat a
    missing key as 0.
    """
    item_ids = list(item_ids)
    if not item_ids:
        return {}

    rows = (
        PlayEvent.objects.filter(item_id__in=item_ids)
        .order_by("item_id", "played_at")
        .values_list("item_id", "played_at")
    )

    gap = session_gap()
    counts = {}
    current_item = None
    previous_played_at = None

    for item_id, played_at in rows:
        if item_id != current_item:
            # First event for this item: one session so far.
            counts[item_id] = 1
            current_item = item_id
        elif played_at - previous_played_at > gap:
            counts[item_id] += 1
        previous_played_at = played_at

    return counts


# SQL mirror of ``ListeningItem.estimated_listened_ms``, so the column can be
# sorted in the database instead of in Python.
#
# These two must agree. Episodes use the furthest position ever observed;
# everything else is observations x duration. A NULL duration_ms propagates to
# NULL here, matching the property's ``return None``.
ESTIMATED_MS = Case(
    When(item_type=ListeningItem.ItemType.EPISODE, then=F("max_progress_ms")),
    default=F("duration_ms") * F("observation_count"),
    output_field=BigIntegerField(),
)


def humanize_ms(milliseconds):
    """Render a duration as a coarse, human string — ``2h 53m``, ``4m``, ``38s``.

    Deliberately coarse. Every duration in this app is inferred rather than
    measured (§2.4: the API publishes no ``ms_played`` anywhere), so rendering
    seconds on a multi-hour estimate would imply precision that does not exist.
    The template adds the ``~``.
    """
    if not milliseconds or milliseconds < 0:
        return ""

    total_seconds = int(milliseconds // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"
