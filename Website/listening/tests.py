"""Tests for the collector.

``requests`` is stubbed at the transport layer rather than the client functions
being mocked out, so these exercise the real status-code handling in
``spotify.py`` (204 / 401 / 429) as well as the sync logic above it.

The payloads are shaped from the actual Phase 0 probe responses — same keys,
same nesting, same quirks (``show.publisher`` absent; ``resume_point`` sitting
on the item while ``progress_ms`` sits at the top level).

The point of most of these is to pin down decisions that are invisible in
ordinary operation: a paused player, a song caught by the poll, and a second run
arriving seconds after the first. Each would silently corrupt the timestamps
that are this app's entire deliverable.
"""

import json
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from listening import spotify, sync
from listening.models import ListeningItem, PlayEvent, SpotifyAuth

EPISODE_ID = "2B5rKwS9q5bfA1Exu9yprp"
SHOW_ID = "3iiyQHm9r6ZuL5N0N1nB3S"
TRACK_ID = "5uQRuSGE3EdDyvwWz5l40q"


def episode_poll(is_playing=True, progress_ms=4733753, resume_ms=4714486, fully_played=False):
    """A currently-playing response with a podcast episode, as probe A returned."""
    return {
        "timestamp": 1786386799008,
        "progress_ms": progress_ms,
        "is_playing": is_playing,
        "currently_playing_type": "episode",
        "item": {
            "id": EPISODE_ID,
            "type": "episode",
            "name": "Evolution on Trial",
            "duration_ms": 10416000,
            "external_urls": {"spotify": f"https://open.spotify.com/episode/{EPISODE_ID}"},
            "images": [
                {"width": 64, "url": "https://i.scdn.co/image/small"},
                {"width": 640, "url": "https://i.scdn.co/image/large"},
                {"width": 300, "url": "https://i.scdn.co/image/medium"},
            ],
            "resume_point": {"fully_played": fully_played, "resume_position_ms": resume_ms},
            # NOTE: no "publisher" key — Spotify does not send one.
            "show": {"id": SHOW_ID, "name": "Modern-Day Debate", "total_episodes": 600},
        },
    }


def track_poll():
    """A currently-playing response with a SONG playing."""
    return {
        "progress_ms": 42000,
        "is_playing": True,
        "currently_playing_type": "track",
        "item": {
            "id": TRACK_ID,
            "type": "track",
            "name": "Some Song",
            "duration_ms": 180000,
            "artists": [{"name": "Niko Thalen"}],
            "album": {"images": [{"width": 640, "url": "https://i.scdn.co/image/cover"}]},
            "external_urls": {"spotify": f"https://open.spotify.com/track/{TRACK_ID}"},
        },
    }


def recent_entry(played_at="2026-08-10T17:49:36.849Z", track_id=TRACK_ID, name="Some Song"):
    return {
        "played_at": played_at,
        "context": None,
        "track": {
            "id": track_id,
            "type": "track",
            "name": name,
            "duration_ms": 180000,
            "artists": [{"name": "Niko Thalen"}, {"name": "Guest"}],
            "album": {"images": [{"width": 640, "url": "https://i.scdn.co/image/cover"}]},
            "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
        },
    }


class _Response:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = "" if payload is None else json.dumps(payload)
        self.content = b"" if payload is None else self.text.encode()

    def json(self):
        return self._payload


@override_settings(
    SPOTIFY_CLIENT_ID="test-client-id",
    SPOTIFY_CLIENT_SECRET="test-client-secret",
    SPOTIFY_REFRESH_TOKEN="test-refresh-token",
)
class SyncTests(TestCase):
    def setUp(self):
        self.currently_playing = None   # None -> HTTP 204 (nothing playing)
        self.recently_played = []
        self.get_overrides = []         # queue of _Response to return first
        self.get_calls = []
        self.token_calls = 0

        for target, handler in (
            ("listening.spotify.requests.get", self._fake_get),
            ("listening.spotify.requests.post", self._fake_post),
        ):
            patcher = mock.patch(target, side_effect=handler)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.now = timezone.now().replace(second=17, microsecond=500000)

    # -- fake transport ----------------------------------------------------

    def _fake_get(self, url, params=None, headers=None, timeout=None):
        self.get_calls.append(url)
        if self.get_overrides:
            return self.get_overrides.pop(0)
        if "currently-playing" in url:
            if self.currently_playing is None:
                return _Response(204)
            return _Response(200, self.currently_playing)
        if "recently-played" in url:
            return _Response(200, {"items": self.recently_played})
        raise AssertionError(f"unexpected URL: {url}")

    def _fake_post(self, url, data=None, headers=None, timeout=None):
        self.token_calls += 1
        return _Response(200, {
            "access_token": f"access-token-{self.token_calls}",
            "expires_in": 3600,
            "scope": " ".join(spotify.SCOPES),
        })

    # -- the poll is the only source of podcast data -----------------------

    def test_episode_poll_creates_item_and_event(self):
        self.currently_playing = episode_poll()

        summary = sync.run_sync(now=self.now)

        self.assertEqual(summary["outcome"], "ok")
        self.assertEqual(summary["episode_observations"], 1)

        item = ListeningItem.objects.get(spotify_id=EPISODE_ID)
        self.assertEqual(item.item_type, ListeningItem.ItemType.EPISODE)
        # The show is the creator: show.publisher is not in the payload.
        self.assertEqual(item.creator_name, "Modern-Day Debate")
        self.assertEqual(item.show_spotify_id, SHOW_ID)
        self.assertEqual(item.image_url, "https://i.scdn.co/image/large")

        event = PlayEvent.objects.get()
        self.assertEqual(event.source, PlayEvent.Source.CURRENTLY_PLAYING)
        # Both position signals are kept: they disagree by ~19s and keeping both
        # makes the listening-time estimate auditable.
        self.assertEqual(event.progress_ms, 4733753)
        self.assertEqual(event.resume_position_ms, 4714486)
        self.assertIs(event.fully_played, False)

    def test_polled_timestamp_is_truncated_to_the_minute(self):
        self.currently_playing = episode_poll()

        sync.run_sync(now=self.now)

        event = PlayEvent.objects.get()
        self.assertEqual(event.played_at.second, 0)
        self.assertEqual(event.played_at.microsecond, 0)

    # -- "running it twice adds no rows", for BOTH sources -----------------

    def test_second_run_adds_no_rows(self):
        self.currently_playing = episode_poll()
        self.recently_played = [recent_entry()]

        sync.run_sync(now=self.now)
        self.assertEqual(PlayEvent.objects.count(), 2)

        # A few seconds later — the realistic "did that work? run it again" case.
        second = sync.run_sync(now=self.now + timezone.timedelta(seconds=31))

        self.assertEqual(second["episode_observations"], 0)
        self.assertEqual(second["track_plays"], 0)
        self.assertEqual(PlayEvent.objects.count(), 2)
        self.assertEqual(ListeningItem.objects.count(), 2)

    def test_later_poll_records_a_new_observation(self):
        """Dedup must not swallow genuine evidence of continued listening."""
        self.currently_playing = episode_poll(progress_ms=1000)
        sync.run_sync(now=self.now)

        self.currently_playing = episode_poll(progress_ms=500000)
        sync.run_sync(now=self.now + timezone.timedelta(minutes=5))

        self.assertEqual(PlayEvent.objects.count(), 2)
        item = ListeningItem.objects.get(spotify_id=EPISODE_ID)
        self.assertEqual(item.observation_count, 2)
        self.assertEqual(item.max_progress_ms, 500000)

    # -- the decisions that would otherwise fail silently ------------------

    def test_paused_player_records_nothing(self):
        """A player left paused must not manufacture listening history."""
        self.currently_playing = episode_poll(is_playing=False)

        summary = sync.run_sync(now=self.now)

        self.assertEqual(summary["skipped"], "paused")
        self.assertEqual(PlayEvent.objects.count(), 0)
        self.assertEqual(ListeningItem.objects.count(), 0)

    def test_song_in_the_poll_is_ignored(self):
        """Songs come from history only — counting them twice wrecks the estimate."""
        self.currently_playing = track_poll()

        summary = sync.run_sync(now=self.now)

        self.assertIn("song is playing", summary["skipped"])
        self.assertEqual(PlayEvent.objects.count(), 0)
        self.assertEqual(ListeningItem.objects.count(), 0)

    def test_nothing_playing_is_a_clean_no_op(self):
        summary = sync.run_sync(now=self.now)

        self.assertEqual(summary["outcome"], "ok")
        self.assertEqual(summary["skipped"], "nothing playing")
        self.assertEqual(PlayEvent.objects.count(), 0)

    # -- history ------------------------------------------------------------

    def test_recently_played_uses_spotifys_own_timestamp(self):
        self.recently_played = [recent_entry(played_at="2026-08-10T17:49:36.849Z")]

        sync.run_sync(now=self.now)

        event = PlayEvent.objects.get()
        self.assertEqual(event.source, PlayEvent.Source.RECENTLY_PLAYED)
        self.assertEqual(event.played_at.isoformat(), "2026-08-10T17:49:36.849000+00:00")
        self.assertEqual(event.item.creator_name, "Niko Thalen, Guest")

    def test_rollups_match_the_event_log(self):
        self.recently_played = [
            recent_entry(played_at="2026-08-09T04:35:51.228Z"),
            recent_entry(played_at="2026-08-10T17:49:36.849Z"),
        ]

        sync.run_sync(now=self.now)

        item = ListeningItem.objects.get(spotify_id=TRACK_ID)
        self.assertEqual(item.observation_count, 2)
        self.assertEqual(item.first_played_at.isoformat(), "2026-08-09T04:35:51.228000+00:00")
        self.assertEqual(item.last_played_at.isoformat(), "2026-08-10T17:49:36.849000+00:00")

    # -- error handling is normal control flow ------------------------------

    def test_401_refreshes_and_retries_exactly_once(self):
        self.currently_playing = episode_poll()
        self.get_overrides = [_Response(401, {"error": {"status": 401, "message": "expired"}})]

        summary = sync.run_sync(now=self.now)

        self.assertEqual(summary["outcome"], "ok")
        self.assertEqual(summary["episode_observations"], 1)
        # Initial mint + one refresh after the rejection. Not more.
        self.assertEqual(self.token_calls, 2)
        self.assertEqual(len(self.get_calls), 3)  # 401'd, retried, then history

    def test_rate_limit_is_transient_and_writes_nothing(self):
        self.get_overrides = [_Response(429, {"error": "slow down"}, {"Retry-After": "12"})]

        summary = sync.run_sync(now=self.now)

        self.assertEqual(summary["outcome"], "transient")
        self.assertEqual(summary["retry_after"], 12)
        self.assertEqual(PlayEvent.objects.count(), 0)
        # The run is still stamped, so a stall stays visible in the admin.
        self.assertIsNotNone(SpotifyAuth.load().last_sync_at)

    def test_network_failure_is_transient_not_fatal(self):
        with mock.patch(
            "listening.spotify.requests.get",
            side_effect=spotify.requests.RequestException("no route to host"),
        ):
            summary = sync.run_sync(now=self.now)

        self.assertEqual(summary["outcome"], "transient")
        self.assertEqual(PlayEvent.objects.count(), 0)

    def test_lapsed_refresh_token_is_fatal(self):
        """The six-month silent death — the one case a human must be told about."""
        with mock.patch(
            "listening.spotify.requests.post",
            return_value=_Response(400, {"error": "invalid_grant"}),
        ):
            summary = sync.run_sync(now=self.now)

        self.assertEqual(summary["outcome"], "fatal")
        self.assertIn("invalid_grant", summary["detail"])

    # -- token bookkeeping --------------------------------------------------

    def test_changed_refresh_token_resets_the_expiry_countdown(self):
        """Production never runs the browser flow, so it must notice this itself."""
        auth = SpotifyAuth.load()
        self.assertIsNone(auth.authorized_at)

        sync.run_sync(now=self.now)

        auth.refresh_from_db()
        self.assertIsNotNone(auth.authorized_at)
        self.assertEqual(auth.days_until_reauth, 179)

    def test_cached_access_token_is_reused(self):
        self.currently_playing = episode_poll()

        sync.run_sync(now=self.now)
        sync.run_sync(now=self.now + timezone.timedelta(minutes=5))

        # One mint for the first run; the second reuses the cached hour-long token.
        self.assertEqual(self.token_calls, 1)

    def test_dry_run_writes_no_listening_data(self):
        self.currently_playing = episode_poll()
        self.recently_played = [recent_entry()]

        summary = sync.run_sync(now=self.now, dry_run=True)

        self.assertTrue(summary["dry_run"])
        self.assertEqual(PlayEvent.objects.count(), 0)
        self.assertEqual(ListeningItem.objects.count(), 0)
        self.assertIsNone(SpotifyAuth.load().last_sync_at)


class AdminStatusTests(TestCase):
    """The two warning displays must render in EVERY state.

    These exist because a crash was found on the healthy branch of
    ``reauth_status``: ``format_html`` escapes its arguments before formatting,
    so a datetime arrives as a string and a ``{:%Y-%m-%d}`` spec raises. It would
    have broken the admin page for roughly 160 of the token's 180 days — and the
    page it breaks is the one built to warn about silent expiry.
    """

    def _admin(self):
        from django.contrib import admin as django_admin
        return django_admin.site._registry[SpotifyAuth]

    def _auth_authorized_days_ago(self, days):
        auth = SpotifyAuth.load()
        auth.authorized_at = timezone.now() - timezone.timedelta(days=days)
        auth.save()
        return auth

    def test_reauth_status_renders_in_every_state(self):
        admin_instance = self._admin()

        never = SpotifyAuth.load()
        self.assertIn("Never authorized", admin_instance.reauth_status(never))

        # Healthy — the branch that was crashing.
        healthy = self._auth_authorized_days_ago(1)
        self.assertIn("OK", admin_instance.reauth_status(healthy))

        soon = self._auth_authorized_days_ago(170)
        self.assertIn("re-authorize soon", admin_instance.reauth_status(soon))

        expired = self._auth_authorized_days_ago(200)
        self.assertIn("EXPIRED", admin_instance.reauth_status(expired))

    def test_collection_status_renders_in_every_state(self):
        admin_instance = self._admin()

        auth = SpotifyAuth.load()
        self.assertIn("Never run", admin_instance.collection_status(auth))

        for hours, expected in ((0, "Ran"), (8, "check the"), (30, "not running")):
            auth.last_sync_at = timezone.now() - timezone.timedelta(hours=hours, minutes=1)
            auth.save()
            self.assertIn(expected, admin_instance.collection_status(auth))

    def test_collection_status_reports_the_newest_observation(self):
        admin_instance = self._admin()
        auth = SpotifyAuth.load()
        auth.last_sync_at = timezone.now()
        auth.save()

        self.assertIn("no observations recorded yet", admin_instance.collection_status(auth))

        item = ListeningItem.objects.create(
            spotify_id=EPISODE_ID,
            item_type=ListeningItem.ItemType.EPISODE,
            name="Evolution on Trial",
        )
        PlayEvent.objects.create(
            item=item,
            played_at=timezone.now() - timezone.timedelta(hours=2),
            source=PlayEvent.Source.CURRENTLY_PLAYING,
        )
        self.assertIn("newest observation", admin_instance.collection_status(auth))
