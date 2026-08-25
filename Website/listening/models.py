"""Models for the listening tracker.

Design notes (see the Spotify Listening Tracker scope, §4):

* ``PlayEvent`` is the append-only raw log — one row per observation, never edited.
* ``ListeningItem`` is the *output* table that gets surfaced: one row per song or
  podcast episode, carrying cached rollups so the table page is a cheap indexed
  read rather than an aggregate over the whole event log.
* ``SpotifyAuth`` is a single row holding short-lived token state. The refresh
  token itself deliberately lives in the environment, NOT here — see below.

Everything must work on SQLite (local) and MySQL (production), so no
engine-specific field types are used.
"""

import hashlib

from django.db import models
from django.utils import timezone

# Spotify refresh tokens last roughly six months, and refreshing an access token
# does NOT extend that. When it lapses, collection stops silently — no error,
# just no new rows — so the admin surfaces a countdown against this figure.
REFRESH_TOKEN_LIFETIME_DAYS = 180


def fingerprint_token(token):
    """Short, non-reversible fingerprint of a refresh token.

    Lets us notice that the token in the environment has changed (i.e. someone
    re-authorized) without ever storing the secret in the database.
    """
    if not token:
        return ""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class SpotifyAuth(models.Model):
    """Singleton row caching Spotify token state.

    The refresh token is NOT stored here. It lives in the environment
    (``SPOTIFY_REFRESH_TOKEN``) so it follows the same handling as every other
    secret in this project: gitignored ``.env`` locally, ``/etc/anotiontoponder.env``
    on the server. Keeping only a fingerprint means the database — which is
    dumped, copied and inspected — never carries the credential.
    """

    # Cache of the 1-hour access token, so a sync run every few minutes doesn't
    # pointlessly re-mint one on every single call.
    access_token = models.TextField(blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)

    # Fingerprint of the refresh token currently in the environment. If the env
    # value changes, we know a re-authorization happened and reset authorized_at.
    refresh_token_fingerprint = models.CharField(max_length=32, blank=True)
    authorized_at = models.DateTimeField(null=True, blank=True)
    scopes = models.TextField(blank=True)

    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Spotify auth"
        verbose_name_plural = "Spotify auth"

    def __str__(self):
        return f"Spotify auth (authorized {self.authorized_at:%Y-%m-%d})" if self.authorized_at else "Spotify auth (never authorized)"

    @classmethod
    def load(cls):
        """Fetch the singleton row, creating it on first use."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def access_token_is_valid(self):
        if not self.access_token or not self.access_token_expires_at:
            return False
        # 60s of slack so a token can't expire mid-request.
        return timezone.now() < self.access_token_expires_at - timezone.timedelta(seconds=60)

    @property
    def refresh_token_expires_at(self):
        if not self.authorized_at:
            return None
        return self.authorized_at + timezone.timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS)

    @property
    def days_until_reauth(self):
        """Days left before the refresh token is expected to lapse.

        Negative means it is overdue. Surfaced in the admin because this failure
        is otherwise completely silent.
        """
        expires = self.refresh_token_expires_at
        if not expires:
            return None
        return (expires - timezone.now()).days


class ListeningItem(models.Model):
    """A distinct song or podcast episode — the row the surfaced table shows."""

    class ItemType(models.TextChoices):
        TRACK = "track", "Song"
        EPISODE = "episode", "Podcast episode"

    spotify_id = models.CharField(max_length=64, unique=True)
    item_type = models.CharField(max_length=16, choices=ItemType.choices)

    name = models.CharField(max_length=500)

    # "Artist(s), or whatever entity the podcast is put out by".
    # Tracks: joined artist names. Episodes: the show name — Spotify does NOT
    # include show.publisher in the payload (verified in the Phase 0 probe), and
    # for a podcast the show is the meaningful entity anyway.
    creator_name = models.CharField(max_length=500, blank=True)

    show_name = models.CharField(max_length=500, blank=True)
    show_spotify_id = models.CharField(max_length=64, blank=True)

    duration_ms = models.BigIntegerField(null=True, blank=True)
    spotify_url = models.URLField(max_length=500, blank=True)
    image_url = models.URLField(max_length=500, blank=True)

    # --- cached rollups, recomputed from PlayEvent as observations arrive ------
    first_played_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_played_at = models.DateTimeField(null=True, blank=True, db_index=True)
    observation_count = models.PositiveIntegerField(default=0)

    # Furthest position ever seen in this item. For episodes this is the least
    # bad estimate of "how much was listened to" — the API publishes no
    # ms_played anywhere, so listening time is always inferred, never measured.
    max_progress_ms = models.BigIntegerField(null=True, blank=True)
    fully_played = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_played_at", "name"]
        indexes = [
            models.Index(fields=["item_type", "-last_played_at"]),
        ]

    def __str__(self):
        return f"{self.name} — {self.creator_name}" if self.creator_name else self.name

    @property
    def estimated_listened_ms(self):
        """Best-effort listening time. An ESTIMATE — never present it as exact.

        Episodes: furthest observed position, which is right for a linear listen
        and wrong if the episode was re-listened or skipped around.
        Tracks: observations x duration, which over-counts every skipped song.
        """
        if self.item_type == self.ItemType.EPISODE:
            return self.max_progress_ms
        if self.duration_ms:
            return self.duration_ms * self.observation_count
        return None

    def recalculate_rollups(self, save=True):
        """Rebuild the cached columns from the raw event log.

        The events are the source of truth; these fields are a cache that exists
        so the table page can sort and filter without aggregating every time.
        """
        events = self.play_events.all()
        aggregate = events.aggregate(
            first=models.Min("played_at"),
            last=models.Max("played_at"),
            count=models.Count("id"),
            furthest=models.Max("progress_ms"),
        )
        self.first_played_at = aggregate["first"]
        self.last_played_at = aggregate["last"]
        self.observation_count = aggregate["count"] or 0
        self.max_progress_ms = aggregate["furthest"]
        self.fully_played = events.filter(fully_played=True).exists()
        if save:
            self.save(update_fields=[
                "first_played_at", "last_played_at", "observation_count",
                "max_progress_ms", "fully_played", "updated_at",
            ])


class PlayEvent(models.Model):
    """One observation that an item was being listened to. Append-only."""

    class Source(models.TextChoices):
        RECENTLY_PLAYED = "recently_played", "Recently played (history)"
        CURRENTLY_PLAYING = "currently_playing", "Currently playing (poll)"

    item = models.ForeignKey(
        ListeningItem,
        on_delete=models.CASCADE,
        related_name="play_events",
    )

    # For recently-played this is Spotify's authoritative played_at. For a poll
    # it is when we observed it, which is the best available truth for podcasts
    # since no history endpoint exists for them.
    played_at = models.DateTimeField(db_index=True)

    source = models.CharField(max_length=32, choices=Source.choices)

    # Episode-only progress signals. progress_ms is the live position and
    # resume_position_ms is Spotify's saved bookmark; the Phase 0 probe found
    # them ~19s apart, so both are kept and progress_ms is preferred.
    progress_ms = models.BigIntegerField(null=True, blank=True)
    resume_position_ms = models.BigIntegerField(null=True, blank=True)
    fully_played = models.BooleanField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-played_at"]
        constraints = [
            # The load-bearing constraint: recently-played returns an
            # overlapping 50-item window on EVERY poll, so without this each run
            # would re-insert everything it had already seen. This is what makes
            # the sync idempotent and safe to put on a timer.
            models.UniqueConstraint(
                fields=["item", "played_at", "source"],
                name="unique_play_observation",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "-played_at"]),
        ]

    def __str__(self):
        return f"{self.item.name} @ {self.played_at:%Y-%m-%d %H:%M}"


class ListeningAccess(models.Model):
    """Permission anchor. No table, no rows, never instantiated.

    Exists only to declare a *page-level* permission. "Can view the listening
    dashboard" isn't a fact about any model row, so hanging it on ``ListeningItem``
    would file it somewhere misleading.

    ``managed = False`` means Django creates no table for this. It still creates
    the ContentType and the Permission row, because ``create_permissions`` walks
    every model in an installed app, managed or not.

    Deliberately NOT registered in admin.py — there is nothing to list.

    WHY A PERMISSION AND NOT ``is_staff``
    -------------------------------------
    ``is_staff`` means "may open Django's /admin/". It is an admin-site flag, not
    a trust level. The parent project plans 3-5 blog authors; the moment one is
    given staff so they can write posts, a staff-gated page would hand them this
    private data in the same act — no code change, no warning. Gating on an
    explicit permission makes admin access and feature access independent.
    """

    class Meta:
        managed = False            # no CREATE TABLE
        default_permissions = ()   # and none of the add/change/delete/view four
        permissions = [("view_dashboard", "Can view the listening dashboard")]
