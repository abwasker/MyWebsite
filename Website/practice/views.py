"""The practice tools: a public page, plus two endpoints that require a login.

Decisions worth knowing before changing anything here:

* **The PAGE is public; the ENDPOINTS are not.** Unlike ``/listening/``, there is
  no permission gate on the page — anyone may practise. Only saving and reading
  progress require an account, and ⚠️ **each endpoint carries its own decorator**
  rather than inheriting one. A second door needs its own lock; that lesson is
  written up in ``listening/views.py``.

* **Progress exists only for signed-in users** (scope §2.5). Anonymous visitors
  persist nothing, so there is no merge, no dedupe and no marker. ⚠️ They still
  see in-session statistics — the browser computes those in memory. What is gated
  is persistence, not display, and the page must say "not saved" rather than
  offering a sign-in that most visitors cannot act on (scope §2.1: public signup
  is closed).

* 🔴 **The POST body is validated, and a bad batch is REJECTED WHOLE.** Never
  partially store a batch and never truncate one: a silently shortened save is
  indistinguishable from a successful one, which is the failure shape this
  project keeps meeting. Validation is all-or-nothing and returns 400.

* **Duplicates are absorbed, not rejected.** ``bulk_create(ignore_conflicts=True)``
  against the ``(user, client_id)`` unique constraint means a re-sent batch — a
  retry after a lost response — is harmless. That is the intended path, not an
  error case.
  ⚠️ ``ignore_conflicts`` also swallows a CheckConstraint violation, so the
  Python validation above is the real guard and the constraint is the backstop.

* **Statistics are computed, never stored.** No summary row, nothing to drift.
"""

import json
import uuid
from functools import wraps

from django.core.cache import cache
from django.db.models import Count, Q
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import PracticeAttempt

# One tool exists today. The model keys rows by slug so a second drill needs no
# migration, but there is deliberately no registry yet — one would be invented
# infrastructure with a single member.
TOOL = PracticeAttempt.DEFAULT_TOOL

# A batch is what accumulates between debounced saves (~1.2s in the browser), so
# it is normally one or two attempts. The cap exists to bound a hostile payload,
# not to shape normal use.
MAX_BATCH = 200

# Bytes. An attempt serialises to well under 150 bytes, so this is generous for
# MAX_BATCH and still far below Django's 2.5 MB default, which is the point:
# the default is sized for file uploads, not for this.
MAX_BODY_BYTES = 64 * 1024

# Requests per minute, and attempts per day. Answering needs listening, so even
# fast practice stays far under both.
MAX_REQUESTS_PER_MINUTE = 60
MAX_ATTEMPTS_PER_DAY = 2000

_REQUIRED_KEYS = {"client_id", "mode", "interval_steps", "correct"}
_VALID_MODES = set(PracticeAttempt.Mode.values)


def json_login_required(view):
    """403 JSON, deliberately NOT Django's ``login_required``.

    ⚠️ ``login_required`` answers with a 302 to an HTML login page. That is right
    for a page and wrong for an endpoint: ``fetch()`` follows the redirect, gets
    the login page's HTML, and then fails while parsing it as JSON — so "you are
    not signed in" would reach the client as a syntax error. A 403 with a body
    the client can read says what actually happened.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "sign in to save progress"}, status=403)
        return view(request, *args, **kwargs)

    return wrapped


def _check_attempt_rate_limit(user_id, batch_size):
    """Mirrors blog.views._check_comment_rate_limit: returns a message or None.

    Two different limits, because they bound different things — a flood of
    requests, and a implausible volume of practice.
    """
    now = timezone.now()
    minute_key = f"practice-attempts-minute-{user_id}-{now.strftime('%Y%m%d%H%M')}"
    daily_key = f"practice-attempts-daily-{user_id}-{now.date().isoformat()}"

    requests_this_minute = cache.get(minute_key, 0)
    if requests_this_minute >= MAX_REQUESTS_PER_MINUTE:
        return "Too many saves in a short time. Wait a moment and keep practising."

    attempts_today = cache.get(daily_key, 0)
    if attempts_today + batch_size > MAX_ATTEMPTS_PER_DAY:
        return "Daily practice-save limit reached. Progress will save again tomorrow."

    cache.set(minute_key, requests_this_minute + 1, timeout=120)
    cache.set(daily_key, attempts_today + batch_size, timeout=86400)
    return None


def _clean_attempt(raw):
    """Validate one attempt. Returns a PracticeAttempt (unsaved) or raises ValueError.

    Strict on purpose. `correct` must be a real bool rather than anything truthy,
    because "1" and "no" would both otherwise read as True.
    """
    if not isinstance(raw, dict):
        raise ValueError("each attempt must be an object")

    missing = _REQUIRED_KEYS - raw.keys()
    if missing:
        raise ValueError(f"missing {', '.join(sorted(missing))}")

    mode = raw["mode"]
    if mode not in _VALID_MODES:
        raise ValueError(f"unknown mode {mode!r}")

    steps = raw["interval_steps"]
    # bool is a subclass of int in Python, so True would otherwise pass as 1.
    if isinstance(steps, bool) or not isinstance(steps, int):
        raise ValueError("interval_steps must be a whole number")
    if not PracticeAttempt.MIN_STEPS <= steps <= PracticeAttempt.MAX_STEPS:
        raise ValueError("interval_steps out of range")

    if not isinstance(raw["correct"], bool):
        raise ValueError("correct must be true or false")

    try:
        client_id = uuid.UUID(str(raw["client_id"]))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("client_id must be a UUID")

    return PracticeAttempt(
        tool=TOOL,
        mode=mode,
        interval_steps=steps,
        correct=raw["correct"],
        client_id=client_id,
    )


def interval_ladder(request):
    """The practice tool itself. PUBLIC — no login, no permission.

    The config goes to the browser through ``json_script``, which escapes it
    properly — never through string interpolation into a <script> block.

    ⚠️ ``get_token`` is called explicitly so the CSRF cookie is set even though
    this page renders no form. Without it the first save would fail with a 403
    for a visitor who signed in elsewhere and arrived here directly.
    """
    config = {
        "authenticated": request.user.is_authenticated,
        "csrfToken": get_token(request),
        "attemptsUrl": reverse("interval-ladder-attempts"),
        "progressUrl": reverse("interval-ladder-progress"),
    }
    return render(request, "practice/interval-ladder.html", {"il_config": config})


@json_login_required
@require_POST
def save_attempts(request):
    """Append a batch of answered questions for the signed-in user."""
    if len(request.body) > MAX_BODY_BYTES:
        return JsonResponse({"error": "payload too large"}, status=413)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "body must be JSON"}, status=400)

    if not isinstance(payload, dict) or not isinstance(payload.get("attempts"), list):
        return JsonResponse({"error": "expected an attempts list"}, status=400)

    raw_attempts = payload["attempts"]
    if not raw_attempts:
        return JsonResponse({"saved": 0, "received": 0})
    if len(raw_attempts) > MAX_BATCH:
        return JsonResponse({"error": f"at most {MAX_BATCH} attempts per batch"}, status=400)

    # All-or-nothing: one bad entry rejects the batch rather than storing a
    # silently shortened version of it.
    try:
        rows = [_clean_attempt(raw) for raw in raw_attempts]
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    limited = _check_attempt_rate_limit(request.user.pk, len(rows))
    if limited:
        return JsonResponse({"error": limited}, status=429)

    for row in rows:
        row.user = request.user

    before = PracticeAttempt.objects.filter(user=request.user, tool=TOOL).count()
    PracticeAttempt.objects.bulk_create(rows, ignore_conflicts=True)
    after = PracticeAttempt.objects.filter(user=request.user, tool=TOOL).count()

    # `saved` may be less than `received` when a batch is re-sent. That is the
    # deduplication working, not a failure, so it is reported rather than hidden.
    return JsonResponse({"saved": after - before, "received": len(rows)})


@json_login_required
@require_GET
def progress(request):
    """Aggregated statistics for the signed-in user. Computed, never stored."""
    attempts = PracticeAttempt.objects.filter(user=request.user, tool=TOOL)

    by_interval = list(
        attempts.values("mode", "interval_steps")
        .annotate(n=Count("id"), c=Count("id", filter=Q(correct=True)))
        .order_by("mode", "interval_steps")
    )

    totals = attempts.aggregate(n=Count("id"), c=Count("id", filter=Q(correct=True)))

    return JsonResponse(
        {
            "tool": TOOL,
            "total": {"n": totals["n"] or 0, "c": totals["c"] or 0},
            "by_interval": by_interval,
        }
    )
