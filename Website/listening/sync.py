"""The collector: turn Spotify's two endpoints into rows.

This lives outside the management command so it can be imported and tested
directly, without shelling out.

WHAT THIS COLLECTS, AND WHY IT IS ASYMMETRIC
--------------------------------------------
The two sources are not interchangeable, and the difference drives most of the
decisions below:

* **Songs** have TWO sources — ``recently-played`` (authoritative history, with
  Spotify's own ``played_at``) and, incidentally, the live poll. The risk is
  double-counting.
* **Podcast episodes** have exactly ONE source — the live poll. ``recently-played``
  cannot return an episode: every entry is wrapped as ``{context, played_at,
  track}``, with no slot an episode could occupy, and the API rejects any other
  type outright (``400 "Bad type field, must be any of [track]"``). The risk is
  missing a listen entirely.

So the poll is not a supplement to the history endpoint — for podcasts it *is*
the dataset. Three rules follow, and each one changes what the stored numbers
mean rather than merely how this code is arranged:

1. **The poll records episodes only.** A song caught mid-play is ignored here,
   because ``recently-played`` will report it with a real timestamp. Recording
   both would inflate a song's estimate by a whole track length per poll, since
   that estimate is ``duration_ms x observation_count``.
2. **A paused player is not a listen.** ``is_playing: false`` is skipped
   entirely; otherwise a player left paused overnight would manufacture ~100
   observations claiming listening at 3am — corrupting precisely the timestamps
   this app exists to record.
3. **A polled timestamp is truncated to the minute.** It is a sample, so
   sub-minute precision would be false precision; and it makes re-running the
   command back-to-back genuinely insert nothing, rather than "nothing, for
   songs".
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import spotify
from .models import ListeningItem, PlayEvent, SpotifyAuth, fingerprint_token

logger = logging.getLogger(__name__)


def _clip(value, length):
    """Defensive truncation to the model's max_length.

    SQLite silently accepts an over-long string; MySQL in strict mode raises.
    Production is MySQL and is not yet exercised (Phase 5), so clipping here
    means an unusually long episode title can never be the thing that takes the
    collector down.
    """
    return (value or "")[:length]


def _largest_image_url(images):
    """Cover art at the best resolution offered (the payload lists 640/300/64)."""
    if not images:
        return ""
    best = max(images, key=lambda image: image.get("width") or 0)
    return best.get("url") or ""


# --------------------------------------------------------------------------
# Token handling
# --------------------------------------------------------------------------

def get_access_token(auth):
    """Return a usable access token, minting one only when the cache is stale."""
    refresh_token = settings.SPOTIFY_REFRESH_TOKEN
    if not refresh_token:
        raise spotify.SpotifyError(
            "SPOTIFY_REFRESH_TOKEN is not set. Run 'manage.py spotify_authorize' "
            "locally, then put the value in .env (or /etc/anotiontoponder.env on "
            "the server)."
        )

    fingerprint = fingerprint_token(refresh_token)
    if fingerprint != auth.refresh_token_fingerprint:
        # The token in the environment is not the one we last saw, so someone
        # re-authorized. This matters most in production, which never runs the
        # browser flow itself (the token is copied in by hand) and would
        # otherwise show a permanently stale expiry countdown — the only warning
        # that exists before collection dies silently at six months.
        logger.info("Spotify refresh token changed; recording a new authorization date.")
        auth.refresh_token_fingerprint = fingerprint
        auth.authorized_at = timezone.now()
        auth.access_token = ""
        auth.access_token_expires_at = None
        auth.save(update_fields=[
            "refresh_token_fingerprint", "authorized_at",
            "access_token", "access_token_expires_at", "updated_at",
        ])

    if auth.access_token_is_valid:
        return auth.access_token
    return mint_access_token(auth)


def mint_access_token(auth):
    """Exchange the refresh token for a fresh 1-hour access token and cache it."""
    payload = spotify.refresh_access_token(settings.SPOTIFY_REFRESH_TOKEN)

    rotated = payload.get("refresh_token")
    if rotated and fingerprint_token(rotated) != auth.refresh_token_fingerprint:
        # Spotify may hand back a new refresh token. We cannot write it to the
        # environment from here (production reads /etc/anotiontoponder.env, owned
        # by root), so say so loudly rather than let the stored one quietly drift
        # out of date.
        logger.warning(
            "Spotify returned a NEW refresh token, which was not saved. If "
            "collection later stops with invalid_grant, re-run "
            "'manage.py spotify_authorize'."
        )

    auth.access_token = payload["access_token"]
    auth.access_token_expires_at = timezone.now() + timezone.timedelta(
        seconds=payload.get("expires_in", 3600)
    )
    if payload.get("scope"):
        auth.scopes = payload["scope"]
    auth.save(update_fields=[
        "access_token", "access_token_expires_at", "scopes", "updated_at",
    ])
    return auth.access_token


def _call(auth, func):
    """Run an API call, refreshing and retrying EXACTLY once on a 401.

    The cached token carries 60s of slack, but it can still be rejected — clock
    skew, a revoked grant, or a token minted before a scope change. One retry
    covers that; more would just hammer a genuinely dead credential.
    """
    try:
        return func(get_access_token(auth))
    except spotify.SpotifyAuthError:
        logger.info("Access token rejected; refreshing and retrying once.")
        auth.access_token = ""
        auth.access_token_expires_at = None
        return func(mint_access_token(auth))


# --------------------------------------------------------------------------
# Mapping payloads to items
# --------------------------------------------------------------------------

def upsert_item_from_episode(payload):
    """Create or refresh the ListeningItem for a podcast episode."""
    show = payload.get("show") or {}
    show_name = _clip(show.get("name"), 500)
    return ListeningItem.objects.update_or_create(
        spotify_id=payload["id"],
        defaults={
            "item_type": ListeningItem.ItemType.EPISODE,
            "name": _clip(payload.get("name"), 500),
            # The show, NOT a publisher: show.publisher simply is not in the
            # payload (verified in the Phase 0 probe), and for a podcast the show
            # is the meaningful entity anyway.
            "creator_name": show_name,
            "show_name": show_name,
            "show_spotify_id": _clip(show.get("id"), 64),
            "duration_ms": payload.get("duration_ms"),
            "spotify_url": _clip((payload.get("external_urls") or {}).get("spotify"), 500),
            "image_url": _clip(_largest_image_url(payload.get("images")), 500),
        },
    )


def upsert_item_from_track(payload):
    """Create or refresh the ListeningItem for a song."""
    album = payload.get("album") or {}
    artists = ", ".join(
        artist.get("name", "") for artist in (payload.get("artists") or []) if artist.get("name")
    )
    return ListeningItem.objects.update_or_create(
        spotify_id=payload["id"],
        defaults={
            "item_type": ListeningItem.ItemType.TRACK,
            "name": _clip(payload.get("name"), 500),
            "creator_name": _clip(artists, 500),
            "duration_ms": payload.get("duration_ms"),
            "spotify_url": _clip((payload.get("external_urls") or {}).get("spotify"), 500),
            "image_url": _clip(_largest_image_url(album.get("images")), 500),
        },
    )


# --------------------------------------------------------------------------
# Deciding what to write
# --------------------------------------------------------------------------

def plan_currently_playing(payload, observed_at):
    """Decide whether this poll is evidence of listening.

    Returns ``(plan, reason)`` — exactly one is populated. ``reason`` explains a
    skip in words fit for the command output, because "nothing was recorded" is
    the normal outcome and should never look like a malfunction.
    """
    if not payload:
        return None, "nothing playing"

    if not payload.get("is_playing"):
        # Loaded but paused. Not a listen — see rule 2 in the module docstring.
        return None, "paused"

    item_payload = payload.get("item")
    if not item_payload:
        return None, "playing something with no item data"

    item_type = item_payload.get("type")
    if item_type != "episode":
        # Songs are collected from recently-played, which has Spotify's own
        # timestamp. Counting them here as well would double the play count.
        return None, "a song is playing (songs are collected from history instead)"

    resume_point = item_payload.get("resume_point") or {}
    return {
        "kind": "episode",
        "payload": item_payload,
        # Our observation time, truncated to the minute: this is a sample, and
        # Spotify's own top-level `timestamp` marks the last playback state
        # change rather than the moment we looked.
        "played_at": observed_at.replace(second=0, microsecond=0),
        "progress_ms": payload.get("progress_ms"),
        "resume_position_ms": resume_point.get("resume_position_ms"),
        "fully_played": resume_point.get("fully_played"),
    }, None


def plan_recently_played(items):
    """Turn history entries into planned writes, newest-first order preserved."""
    plans = []
    for entry in items or []:
        track = entry.get("track") or {}
        played_at = parse_datetime(entry.get("played_at") or "")
        if not track.get("id") or not played_at:
            continue
        plans.append({
            "kind": "track",
            "payload": track,
            # Authoritative: Spotify's record of when this actually played.
            "played_at": played_at,
            "progress_ms": None,
            "resume_position_ms": None,
            "fully_played": None,
        })
    return plans


def persist(plans):
    """Write planned observations, skipping any already recorded.

    ``get_or_create`` keyed on exactly the model's unique constraint
    ``(item, played_at, source)`` is what makes this safe on a timer:
    ``recently-played`` returns an overlapping window on every single call, so
    without it each run would re-insert everything it had already seen.

    It is used in preference to ``bulk_create(ignore_conflicts=True)`` because we
    need to know *which* items actually gained an event — only those need their
    rollups recomputed, and ``ignore_conflicts`` will not tell you.
    """
    counts = {"episode": 0, "track": 0}
    items_created = 0
    touched = {}

    with transaction.atomic():
        for plan in plans:
            if plan["kind"] == "episode":
                item, created = upsert_item_from_episode(plan["payload"])
                source = PlayEvent.Source.CURRENTLY_PLAYING
            else:
                item, created = upsert_item_from_track(plan["payload"])
                source = PlayEvent.Source.RECENTLY_PLAYED
            items_created += int(created)

            _, event_created = PlayEvent.objects.get_or_create(
                item=item,
                played_at=plan["played_at"],
                source=source,
                defaults={
                    "progress_ms": plan["progress_ms"],
                    "resume_position_ms": plan["resume_position_ms"],
                    "fully_played": plan["fully_played"],
                },
            )
            if event_created:
                counts[plan["kind"]] += 1
                touched[item.pk] = item

        for item in touched.values():
            item.recalculate_rollups()

    return counts, items_created, len(touched)


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def run_sync(limit=50, dry_run=False, now=None):
    """Fetch both endpoints and record what they show. Returns a summary dict.

    ``outcome`` is one of:

    * ``ok``        — ran to completion (recording nothing is a normal outcome)
    * ``transient`` — rate limited, offline, or a Spotify 5xx. Expected on a
      timer; the next run simply tries again.
    * ``fatal``     — needs a human: missing credentials, or a refresh token that
      has lapsed or been revoked.
    """
    observed_at = now or timezone.now()
    summary = {
        "outcome": "ok",
        "status": "",
        "detail": "",
        "now_playing": None,
        "skipped": None,
        "episode_observations": 0,
        "track_plays": 0,
        "items_created": 0,
        "items_updated": 0,
        "retry_after": None,
        "dry_run": dry_run,
    }

    auth = SpotifyAuth.load()

    try:
        current = _call(auth, spotify.get_currently_playing)
        recent = _call(auth, lambda token: spotify.get_recently_played(token, limit))
    except spotify.SpotifyRateLimited as exc:
        # Honour the backoff by doing nothing at all this run. At a few hundred
        # calls a day against a rolling 30-second window this should never fire.
        summary.update(outcome="transient", status="rate limited", detail=str(exc),
                       retry_after=exc.retry_after)
        logger.warning("Spotify rate limited the collector: %s", exc)
        _record_run(auth, summary, dry_run)
        return summary
    except spotify.SpotifyUnavailable as exc:
        summary.update(outcome="transient", status="unavailable", detail=str(exc))
        logger.warning("Spotify unreachable: %s", exc)
        _record_run(auth, summary, dry_run)
        return summary
    except spotify.SpotifyError as exc:
        summary.update(outcome="fatal", status="failed", detail=str(exc))
        logger.error("Spotify sync failed: %s", exc)
        _record_run(auth, summary, dry_run)
        return summary

    plan, skip_reason = plan_currently_playing(current, observed_at)
    summary["skipped"] = skip_reason
    if plan:
        show = (plan["payload"].get("show") or {}).get("name", "")
        summary["now_playing"] = f"{plan['payload'].get('name', '')} — {show}".strip(" —")

    plans = ([plan] if plan else []) + plan_recently_played(recent)

    if dry_run:
        # Report intent without touching the listening data. (The cached access
        # token may still have been refreshed above — that is operational state,
        # not collected data.)
        summary.update(
            status="dry run",
            detail=f"{len(plans)} observation(s) would be considered",
        )
        return summary

    counts, items_created, items_updated = persist(plans)
    summary.update(
        episode_observations=counts["episode"],
        track_plays=counts["track"],
        items_created=items_created,
        items_updated=items_updated,
        status=_describe(counts, skip_reason),
    )
    _record_run(auth, summary, dry_run)
    return summary


def _describe(counts, skip_reason):
    parts = []
    if counts["episode"]:
        parts.append(f"{counts['episode']} episode obs")
    if counts["track"]:
        parts.append(f"{counts['track']} song play(s)")
    if not parts:
        return f"no new rows ({skip_reason})" if skip_reason else "no new rows"
    return ", ".join(parts)


def _record_run(auth, summary, dry_run):
    """Stamp the run on the singleton so a stall is visible in the admin.

    Written on failure as well as success — a status that stops updating is
    itself the signal that something is wrong.
    """
    if dry_run:
        return
    auth.last_sync_at = timezone.now()
    auth.last_sync_status = _clip(
        summary["status"] if summary["outcome"] == "ok"
        else f"{summary['status']}: {summary['detail']}",
        255,
    )
    auth.save(update_fields=["last_sync_at", "last_sync_status", "updated_at"])
