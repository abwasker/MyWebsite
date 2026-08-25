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

import csv
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from listening import spotify, stats, sync
from listening.models import (
    REFRESH_TOKEN_LIFETIME_DAYS,
    ListeningItem,
    PlayEvent,
    SpotifyAuth,
)

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


TEST_PASSWORD = "pw-for-test-only"


def listening_permission():
    """The custom page-level permission declared on ListeningAccess."""
    return Permission.objects.get(
        codename="view_dashboard", content_type__app_label="listening"
    )


def user_with_listening_access(username="owner", **flags):
    """A user who may see the dashboard because of a PERMISSION, not a flag.

    Note these users are deliberately created WITHOUT is_staff: the whole point
    of the gate is that feature access no longer rides on the admin-site flag.
    """
    user = User.objects.create_user(username=username, password=TEST_PASSWORD, **flags)
    user.user_permissions.add(listening_permission())
    return user


def user_without_listening_access(username="blogauthor", **flags):
    """A stand-in for a future blog author: an account with no listening rights."""
    return User.objects.create_user(username=username, password=TEST_PASSWORD, **flags)


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

        # The stored data is deterministic, so assert on that precisely.
        self.assertEqual(
            auth.refresh_token_expires_at,
            auth.authorized_at + timezone.timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS),
        )

        # days_until_reauth is not deterministic: it measures authorized_at
        # against a SECOND, later call to timezone.now(), so the true remaining
        # life is "180 days minus an infinitesimal" and .days truncates to 179 or
        # 180 depending on whether the clock ticked in between. This test used to
        # assert 179 exactly and therefore failed or passed on timing alone.
        self.assertIn(
            auth.days_until_reauth,
            (REFRESH_TOKEN_LIFETIME_DAYS - 1, REFRESH_TOKEN_LIFETIME_DAYS),
        )

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


class SessionGroupingTests(TestCase):
    """The sessions-not-observations rule (scope §4).

    This is the correction that makes the table readable: a 3-hour podcast
    polled every 5 minutes produces ~36 events and exactly one listen. There is
    no multi-observation item in the real dev database yet — the Phase 4 soak is
    what produces one — so these build the event spacing synthetically.
    """

    def _episode(self, spotify_id=EPISODE_ID):
        return ListeningItem.objects.create(
            spotify_id=spotify_id,
            item_type=ListeningItem.ItemType.EPISODE,
            name="Evolution on Trial",
            creator_name="Whaddo You Meme??",
            duration_ms=10413062,
        )

    def _observe(self, item, base, *offsets_minutes):
        for offset in offsets_minutes:
            PlayEvent.objects.create(
                item=item,
                played_at=base + timezone.timedelta(minutes=offset),
                source=PlayEvent.Source.CURRENTLY_PLAYING,
            )

    def test_one_long_listen_counts_as_a_single_session(self):
        item = self._episode()
        base = timezone.now() - timezone.timedelta(hours=4)
        # 36 polls, 5 minutes apart — a single three-hour sitting.
        self._observe(item, base, *range(0, 180, 5))

        item.recalculate_rollups()
        self.assertEqual(item.observation_count, 36)
        self.assertEqual(stats.session_counts([item.pk])[item.pk], 1)

    def test_a_gap_longer_than_the_threshold_starts_a_new_session(self):
        item = self._episode()
        base = timezone.now() - timezone.timedelta(hours=6)
        # Two sittings an hour apart.
        self._observe(item, base, 0, 5, 10, 70, 75)

        self.assertEqual(stats.session_counts([item.pk])[item.pk], 2)

    def test_jitter_below_the_threshold_does_not_split_a_session(self):
        item = self._episode()
        base = timezone.now() - timezone.timedelta(hours=2)
        # A missed poll or two: 11 minutes is still inside the 15-minute gap.
        self._observe(item, base, 0, 5, 16, 27)

        self.assertEqual(stats.session_counts([item.pk])[item.pk], 1)

    def test_sessions_are_counted_per_item_not_across_them(self):
        first = self._episode()
        second = self._episode(spotify_id="second-episode-id")
        base = timezone.now() - timezone.timedelta(hours=3)
        self._observe(first, base, 0, 5)
        self._observe(second, base, 1, 6)

        counts = stats.session_counts([first.pk, second.pk])
        self.assertEqual(counts[first.pk], 1)
        self.assertEqual(counts[second.pk], 1)

    def test_item_with_no_events_is_absent_rather_than_zero(self):
        item = self._episode()
        self.assertEqual(stats.session_counts([item.pk]), {})

    def test_no_items_makes_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(stats.session_counts([]), {})

    def test_gap_threshold_is_configurable(self):
        item = self._episode()
        base = timezone.now() - timezone.timedelta(hours=5)
        # A 30-minute break: longer than the 15-minute default, shorter than a
        # 60-minute override. The same events must therefore count differently.
        self._observe(item, base, 0, 5, 35, 40)

        self.assertEqual(stats.session_counts([item.pk])[item.pk], 2)

        with override_settings(LISTENING_SESSION_GAP_MINUTES=60):
            self.assertEqual(stats.session_counts([item.pk])[item.pk], 1)


class EstimatedTimeTests(TestCase):
    """The SQL annotation must agree with the model property.

    They are two expressions of the same rule — one for sorting in the database,
    one for display — and a drift between them would show one number in the
    column and sort by another.
    """

    def test_annotation_matches_the_model_property(self):
        episode = ListeningItem.objects.create(
            spotify_id=EPISODE_ID,
            item_type=ListeningItem.ItemType.EPISODE,
            name="Episode",
            duration_ms=10413062,
            max_progress_ms=5193353,
        )
        track = ListeningItem.objects.create(
            spotify_id=TRACK_ID,
            item_type=ListeningItem.ItemType.TRACK,
            name="Track",
            duration_ms=207081,
            observation_count=3,
        )
        no_duration = ListeningItem.objects.create(
            spotify_id="no-duration-id",
            item_type=ListeningItem.ItemType.TRACK,
            name="Unknown length",
            observation_count=2,
        )

        annotated = {
            row.pk: row.est_ms
            for row in ListeningItem.objects.annotate(est_ms=stats.ESTIMATED_MS)
        }

        self.assertEqual(annotated[episode.pk], episode.estimated_listened_ms)
        self.assertEqual(annotated[track.pk], track.estimated_listened_ms)
        self.assertEqual(annotated[episode.pk], 5193353)
        self.assertEqual(annotated[track.pk], 207081 * 3)
        # A missing duration must not become 0 — that would read as "listened to
        # for no time" rather than "unknown".
        self.assertIsNone(annotated[no_duration.pk])
        self.assertIsNone(no_duration.estimated_listened_ms)

    def test_humanize_ms_is_coarse_and_safe(self):
        self.assertEqual(stats.humanize_ms(5193353), "1h 26m")
        self.assertEqual(stats.humanize_ms(207081), "3m")
        self.assertEqual(stats.humanize_ms(38000), "38s")
        # No duration must render as empty so the template can show a dash.
        self.assertEqual(stats.humanize_ms(None), "")
        self.assertEqual(stats.humanize_ms(0), "")
        self.assertEqual(stats.humanize_ms(-5), "")


class ListeningPageAccessTests(TestCase):
    """Access control on /listening/ (scope §4.3, parent scope §4.8.1).

    The gate is the custom permission ``listening.view_dashboard``, NOT ``is_staff``.
    That distinction is the whole point, and it is the reason this class is worth
    reading: ``is_staff`` only ever meant "may open Django's /admin/". The parent
    project plans 3-5 blog authors, so the moment one is given staff to write posts,
    a staff-gated page would hand them private listening data in the same act — no
    error, no log, no symptom. Only a test ever sees it.

    Note also that none of these can be verified from Anosh's own session: he is a
    superuser, and superusers bypass every permission check, so a working gate and
    a broken one look identical from there.
    """

    def setUp(self):
        self.url = reverse("listening-home")

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(self.url)
        # login_required is the OUTER decorator, so it wins before the permission
        # check can turn this into a bare 403.
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_authenticated_user_without_permission_is_forbidden(self):
        user_without_listening_access()
        self.client.login(username="blogauthor", password=TEST_PASSWORD)

        response = self.client.get(self.url)
        # 403, not a redirect: they ARE logged in, so bouncing them to a login
        # form would be a dead end.
        self.assertEqual(response.status_code, 403)

    def test_staff_alone_no_longer_grants_access(self):
        """THE test for this change.

        A staff account with no listening permission must be refused. Before the
        permission existed this exact user was allowed in, so if this ever goes
        green-by-accident the gate has regressed to the old flag.
        """
        User.objects.create_user(
            username="futureauthor", password=TEST_PASSWORD, is_staff=True
        )
        self.client.login(username="futureauthor", password=TEST_PASSWORD)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_permission_without_staff_is_enough(self):
        """The other half: feature access does not depend on admin access.

        This user cannot open /admin/ at all, and can still read the dashboard.
        """
        user = user_with_listening_access()
        self.assertFalse(user.is_staff)
        self.client.login(username="owner", password=TEST_PASSWORD)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "listening/listening_home.html")

    def test_permission_granted_through_a_group_is_enough(self):
        """Groups are how access will actually be handed out (§4.8.5), so the
        group path needs its own test — a direct grant proves less."""
        group = Group.objects.create(name="Listening")
        group.permissions.add(listening_permission())

        user = User.objects.create_user(username="viewer", password=TEST_PASSWORD)
        user.groups.add(group)

        self.client.login(username="viewer", password=TEST_PASSWORD)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_removing_the_group_revokes_access(self):
        """Revocation has to work, not just granting."""
        group = Group.objects.create(name="Listening")
        group.permissions.add(listening_permission())
        user = User.objects.create_user(username="viewer", password=TEST_PASSWORD)
        user.groups.add(group)
        self.client.login(username="viewer", password=TEST_PASSWORD)
        self.assertEqual(self.client.get(self.url).status_code, 200)

        user.groups.remove(group)

        # The per-request permission cache lives on the request's own user object,
        # so a fresh request sees the change immediately.
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_superuser_bypasses_the_permission(self):
        """Documents the bypass rather than leaving it as folklore.

        A superuser holds no listening permission and still gets in, because
        has_perm() short-circuits to True. This is why prod (one superuser) sees
        no behavioural change from this work at all.
        """
        superuser = User.objects.create_superuser(
            username="anosh", email="a@example.com", password=TEST_PASSWORD
        )
        self.assertFalse(superuser.user_permissions.exists())
        self.client.login(username="anosh", password=TEST_PASSWORD)

        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_inactive_user_is_locked_out_despite_holding_the_permission(self):
        """is_active is the kill switch: ModelBackend refuses every permission."""
        user = user_with_listening_access()
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 200)

        user.is_active = False
        user.save(update_fields=["is_active"])

        # Deactivation invalidates the session too, so this is a redirect rather
        # than a 403 — the account cannot authenticate at all any more.
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_page_is_not_advertised_in_the_navigation(self):
        user_with_listening_access()
        self.client.login(username="owner", password=TEST_PASSWORD)

        response = self.client.get(reverse("home"))
        self.assertNotContains(response, 'href="/listening/"')


class ListeningPageContentTests(TestCase):
    """Rendering, filtering, sorting and pagination of the table."""

    @classmethod
    def setUpTestData(cls):
        cls.user = user_with_listening_access()
        base = timezone.now() - timezone.timedelta(days=1)

        cls.episode = ListeningItem.objects.create(
            spotify_id=EPISODE_ID,
            item_type=ListeningItem.ItemType.EPISODE,
            name="Evolution on Trial",
            creator_name="Whaddo You Meme??",
            duration_ms=10413062,
            spotify_url="https://open.spotify.com/episode/x",
        )
        cls.track = ListeningItem.objects.create(
            spotify_id=TRACK_ID,
            item_type=ListeningItem.ItemType.TRACK,
            name="The Celestial Flute",
            creator_name="Niko Thalen",
            duration_ms=207081,
        )

        # The episode gets two sittings; the track a single play.
        for offset in (0, 5, 10, 90, 95):
            PlayEvent.objects.create(
                item=cls.episode,
                played_at=base + timezone.timedelta(minutes=offset),
                source=PlayEvent.Source.CURRENTLY_PLAYING,
                progress_ms=5193353,
            )
        PlayEvent.objects.create(
            item=cls.track,
            played_at=base + timezone.timedelta(minutes=30),
            source=PlayEvent.Source.RECENTLY_PLAYED,
        )
        cls.episode.recalculate_rollups()
        cls.track.recalculate_rollups()

    def setUp(self):
        self.client.login(username="owner", password="pw-for-test-only")
        self.url = reverse("listening-home")

    def test_table_shows_sessions_rather_than_observation_count(self):
        response = self.client.get(self.url)
        rows = {row["item"].pk: row for row in response.context["rows"]}

        self.episode.refresh_from_db()
        self.assertEqual(self.episode.observation_count, 5)
        # Five observations, two sittings.
        self.assertEqual(rows[self.episode.pk]["sessions"], 2)
        self.assertEqual(rows[self.track.pk]["sessions"], 1)

    def test_estimated_time_is_rendered_with_a_tilde(self):
        response = self.client.get(self.url)
        self.assertContains(response, "~1h 26m")

    def test_type_filter_narrows_to_one_kind(self):
        response = self.client.get(self.url, {"type": "episode"})
        names = [row["item"].name for row in response.context["rows"]]
        self.assertEqual(names, ["Evolution on Trial"])

        response = self.client.get(self.url, {"type": "track"})
        names = [row["item"].name for row in response.context["rows"]]
        self.assertEqual(names, ["The Celestial Flute"])

    def test_search_matches_name_or_creator(self):
        response = self.client.get(self.url, {"q": "celestial"})
        self.assertEqual(len(response.context["rows"]), 1)

        response = self.client.get(self.url, {"q": "Whaddo"})
        self.assertEqual(len(response.context["rows"]), 1)

        response = self.client.get(self.url, {"q": "nothing here"})
        self.assertEqual(len(response.context["rows"]), 0)
        self.assertContains(response, "Nothing matches that filter")

    def test_sort_by_name_ascending(self):
        response = self.client.get(self.url, {"sort": "name", "dir": "asc"})
        names = [row["item"].name for row in response.context["rows"]]
        self.assertEqual(names, sorted(names))

    def test_sort_by_sessions_orders_by_the_computed_value(self):
        response = self.client.get(self.url, {"sort": "sessions", "dir": "desc"})
        sessions = [row["sessions"] for row in response.context["rows"]]
        self.assertEqual(sessions, [2, 1])

        response = self.client.get(self.url, {"sort": "sessions", "dir": "asc"})
        sessions = [row["sessions"] for row in response.context["rows"]]
        self.assertEqual(sessions, [1, 2])

    def test_unknown_sort_key_falls_back_instead_of_reaching_the_orm(self):
        # A crafted sort key must not become an order_by() argument.
        response = self.client.get(self.url, {"sort": "item__play_events__id"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "last")

    def test_unknown_type_filter_falls_back_to_all(self):
        response = self.client.get(self.url, {"type": "../etc/passwd"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["item_type"], "all")
        self.assertEqual(len(response.context["rows"]), 2)

    def test_pagination_preserves_the_active_filter(self):
        response = self.client.get(self.url, {"type": "episode", "q": "Evolution"})
        preserved = response.context["preserved_query"]
        self.assertIn("type=episode", preserved)
        self.assertIn("q=Evolution", preserved)
        self.assertNotIn("page=", preserved)

    def test_empty_database_renders_a_hint_rather_than_an_empty_table(self):
        ListeningItem.objects.all().delete()
        response = self.client.get(self.url)
        self.assertContains(response, "No listening collected yet")


class ExportListeningTests(TestCase):
    """The export command (Phase 3c).

    Two of these pin down bugs that real data actually caused rather than
    hypotheticals: a podcast title containing a literal ``|`` (which silently
    shifts every Markdown column after it), and non-ASCII creator names (which
    killed stdout on a cp1252 Windows console).
    """

    @classmethod
    def setUpTestData(cls):
        base = timezone.now() - timezone.timedelta(days=1)

        cls.episode = ListeningItem.objects.create(
            spotify_id=EPISODE_ID,
            item_type=ListeningItem.ItemType.EPISODE,
            # The real title of the first episode collected.
            name="Evolution on Trial | @MadebyJimbob Vs @PolymathWorld",
            creator_name="Modern-Day Debate",
            duration_ms=10413062,
        )
        cls.track = ListeningItem.objects.create(
            spotify_id=TRACK_ID,
            item_type=ListeningItem.ItemType.TRACK,
            # Cyrillic С, an accent, and a ć — all present in the real library
            # and all outside cp1252's range for the last one.
            name="Сomfort",
            creator_name="Dalibor Bukvić, Wilson Trouvé",
            duration_ms=484417,
        )

        for offset in (0, 5, 10, 90):
            PlayEvent.objects.create(
                item=cls.episode,
                played_at=base + timezone.timedelta(minutes=offset),
                source=PlayEvent.Source.CURRENTLY_PLAYING,
                progress_ms=5193353,
            )
        PlayEvent.objects.create(
            item=cls.track,
            played_at=base + timezone.timedelta(minutes=30),
            source=PlayEvent.Source.RECENTLY_PLAYED,
        )
        cls.episode.recalculate_rollups()
        cls.track.recalculate_rollups()

    def export(self, **options):
        out = StringIO()
        err = StringIO()
        call_command("export_listening", stdout=out, stderr=err, **options)
        return out.getvalue(), err.getvalue()

    def test_csv_has_the_expected_header(self):
        output, _ = self.export()
        first_line = output.splitlines()[0]
        self.assertEqual(
            first_line,
            "name,creator,type,first_listened,last_listened,listens,estimated_ms,estimated",
        )

    def test_csv_carries_every_item(self):
        output, _ = self.export()
        rows = list(csv.DictReader(StringIO(output)))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["name"] for row in rows}, {self.episode.name, self.track.name})

    def test_csv_preserves_non_ascii(self):
        output, _ = self.export()
        self.assertIn("Сomfort", output)
        self.assertIn("Bukvić", output)
        self.assertIn("Trouvé", output)

    def test_csv_quotes_a_creator_containing_commas(self):
        output, _ = self.export()
        rows = {row["name"]: row for row in csv.DictReader(StringIO(output))}
        # Round-tripping through the csv reader is the real assertion: if the
        # commas weren't quoted, the columns would have shifted.
        self.assertEqual(rows["Сomfort"]["creator"], "Dalibor Bukvić, Wilson Trouvé")

    def test_csv_timestamps_are_iso_with_an_offset(self):
        output, _ = self.export()
        rows = {row["name"]: row for row in csv.DictReader(StringIO(output))}
        stamp = rows["Сomfort"]["last_listened"]
        self.assertIn("T", stamp)
        # Offset present in some form, so the value is never ambiguous.
        self.assertTrue(stamp.endswith("+00:00") or "-" in stamp[10:] or "+" in stamp[10:])

    def test_csv_carries_both_raw_and_human_durations(self):
        output, _ = self.export()
        rows = {row["name"]: row for row in csv.DictReader(StringIO(output))}
        self.assertEqual(rows[self.episode.name]["estimated_ms"], "5193353")
        self.assertEqual(rows[self.episode.name]["estimated"], "1h 26m")

    def test_markdown_escapes_a_pipe_in_a_title(self):
        output, _ = self.export(format="markdown")
        self.assertIn("Evolution on Trial \\| @MadebyJimbob", output)
        # Every row must have the same number of cell separators as the header,
        # which is exactly what an unescaped pipe would break.
        table_lines = [line for line in output.splitlines() if line.startswith("| ")]
        widths = {line.count(" | ") for line in table_lines}
        self.assertEqual(len(widths), 1, f"ragged markdown table: {widths}")

    def test_markdown_marks_estimates_with_a_tilde(self):
        output, _ = self.export(format="markdown")
        self.assertIn("~1h 26m", output)

    def test_markdown_carries_the_same_caveats_as_the_page(self):
        output, _ = self.export(format="markdown")
        self.assertIn("estimate, not a measurement", output)
        self.assertIn("sessions, not polls", output)
        self.assertIn("15", output)  # the configured session gap

    def test_export_sessions_agree_with_the_page(self):
        """The export must not quietly disagree with what the screen shows."""
        user = user_with_listening_access()
        self.client.force_login(user)
        response = self.client.get(reverse("listening-home"))
        page_sessions = {row["item"].name: row["sessions"] for row in response.context["rows"]}

        output, _ = self.export()
        csv_sessions = {
            row["name"]: int(row["listens"]) for row in csv.DictReader(StringIO(output))
        }

        self.assertEqual(page_sessions, csv_sessions)
        # And the value is the session count, not the observation count.
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.observation_count, 4)
        self.assertEqual(csv_sessions[self.episode.name], 2)

    def test_type_filter_limits_the_export(self):
        output, _ = self.export(type="episode")
        rows = list(csv.DictReader(StringIO(output)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "Podcast episode")

    def test_empty_result_warns_instead_of_failing(self):
        ListeningItem.objects.all().delete()
        output, errors = self.export()
        self.assertEqual(output, "")
        self.assertIn("nothing exported", errors.lower())

    def test_csv_file_gets_a_bom_and_markdown_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            csv_path = Path(folder) / "listening.csv"
            md_path = Path(folder) / "listening.md"

            self.export(output=str(csv_path))
            self.export(format="markdown", output=str(md_path))

            # The BOM is what makes Excel read the accents rather than mojibake.
            self.assertTrue(csv_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertFalse(md_path.read_bytes().startswith(b"\xef\xbb\xbf"))

            # Both must be decodable as UTF-8 with the non-ASCII intact.
            self.assertIn("Bukvić", csv_path.read_text(encoding="utf-8-sig"))
            self.assertIn("Bukvić", md_path.read_text(encoding="utf-8"))

    def test_written_file_reports_what_it_wrote(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.csv"
            output, _ = self.export(output=str(path))
            self.assertIn("Wrote 2 item(s)", output)

    def test_unwritable_destination_is_a_command_error(self):
        # A directory path can't be opened for writing as a file.
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(CommandError):
                self.export(output=folder)


class ListeningDownloadTests(TestCase):
    """The download buttons (Phase 3d).

    The download is a second door to the same private data, so the access tests
    here matter as much as the ones on the page: gating the page while leaving
    the export URL open is a textbook way to leak.
    """

    @classmethod
    def setUpTestData(cls):
        base = timezone.now() - timezone.timedelta(days=1)

        cls.episode = ListeningItem.objects.create(
            spotify_id=EPISODE_ID,
            item_type=ListeningItem.ItemType.EPISODE,
            name="Evolution on Trial | @MadebyJimbob Vs @PolymathWorld",
            creator_name="Modern-Day Debate",
            duration_ms=10413062,
        )
        cls.track = ListeningItem.objects.create(
            spotify_id=TRACK_ID,
            item_type=ListeningItem.ItemType.TRACK,
            name="Сomfort",
            creator_name="Dalibor Bukvić",
            duration_ms=484417,
        )

        for offset in (0, 5, 90):
            PlayEvent.objects.create(
                item=cls.episode,
                played_at=base + timezone.timedelta(minutes=offset),
                source=PlayEvent.Source.CURRENTLY_PLAYING,
                progress_ms=5193353,
            )
        PlayEvent.objects.create(
            item=cls.track,
            played_at=base + timezone.timedelta(minutes=30),
            source=PlayEvent.Source.RECENTLY_PLAYED,
        )
        cls.episode.recalculate_rollups()
        cls.track.recalculate_rollups()

    def url(self, output_format="csv"):
        return reverse("listening-download", args=[output_format])

    def login_permitted(self):
        user = user_with_listening_access()
        self.client.force_login(user)
        return user

    def body(self, response):
        return response.content.decode("utf-8-sig")

    # --- access control ---------------------------------------------------

    def test_anonymous_visitor_cannot_download(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_authenticated_user_without_permission_cannot_download(self):
        user_without_listening_access()
        self.client.login(username="blogauthor", password=TEST_PASSWORD)

        for output_format in ("csv", "markdown"):
            response = self.client.get(self.url(output_format))
            self.assertEqual(
                response.status_code, 403,
                f"{output_format} download was not refused without the permission",
            )

    def test_staff_alone_cannot_download(self):
        """The download is its own door; staff must not open it either.

        Symmetry with test_staff_alone_no_longer_grants_access. Gating the page
        and leaving the export reachable is the exact shape of leak this project
        has already shipped once (2026-08-19, the filter-ignoring download).
        """
        User.objects.create_user(
            username="futureauthor", password=TEST_PASSWORD, is_staff=True
        )
        self.client.login(username="futureauthor", password=TEST_PASSWORD)

        for output_format in ("csv", "markdown"):
            self.assertEqual(
                self.client.get(self.url(output_format)).status_code, 403,
                f"{output_format} download was reachable with is_staff alone",
            )

    def test_unknown_format_is_a_404(self):
        self.login_permitted()
        response = self.client.get(self.url("xlsx"))
        self.assertEqual(response.status_code, 404)

    # --- response shape ---------------------------------------------------

    def test_csv_download_headers(self):
        self.login_permitted()
        response = self.client.get(self.url("csv"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", response["Content-Disposition"])
        stamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
        self.assertIn(f'filename="listening-{stamp}.csv"', response["Content-Disposition"])

    def test_markdown_download_headers(self):
        self.login_permitted()
        response = self.client.get(self.url("markdown"))

        self.assertEqual(response["Content-Type"], "text/markdown; charset=utf-8")
        stamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
        self.assertIn(f'filename="listening-{stamp}.md"', response["Content-Disposition"])

    def test_csv_download_starts_with_a_bom(self):
        self.login_permitted()
        response = self.client.get(self.url("csv"))
        # Without it, Excel reads the accented names in the local codepage.
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))

    def test_markdown_download_has_no_bom(self):
        self.login_permitted()
        response = self.client.get(self.url("markdown"))
        self.assertFalse(response.content.startswith(b"\xef\xbb\xbf"))

    def test_download_preserves_non_ascii(self):
        self.login_permitted()
        response = self.client.get(self.url("csv"))
        self.assertIn("Bukvić", self.body(response))
        self.assertIn("Сomfort", self.body(response))

    # --- THE point of this phase: the download follows the filter ---------

    def test_type_filter_is_honoured(self):
        self.login_permitted()

        response = self.client.get(self.url("csv"), {"type": "episode"})
        rows = list(csv.DictReader(StringIO(self.body(response))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "Podcast episode")

        response = self.client.get(self.url("csv"), {"type": "track"})
        rows = list(csv.DictReader(StringIO(self.body(response))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Сomfort")

    def test_search_filter_is_honoured(self):
        self.login_permitted()
        response = self.client.get(self.url("csv"), {"q": "Bukvić"})
        rows = list(csv.DictReader(StringIO(self.body(response))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["creator"], "Dalibor Bukvić")

    def test_filename_names_the_active_filter(self):
        self.login_permitted()
        response = self.client.get(self.url("csv"), {"type": "episode"})
        self.assertIn("listening-episode-", response["Content-Disposition"])

    def test_unfiltered_download_contains_everything(self):
        self.login_permitted()
        response = self.client.get(self.url("csv"))
        rows = list(csv.DictReader(StringIO(self.body(response))))
        self.assertEqual(len(rows), 2)

    def test_bogus_filter_values_fall_back_rather_than_erroring(self):
        self.login_permitted()
        response = self.client.get(self.url("csv"), {"type": "../../etc/passwd"})
        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(StringIO(self.body(response))))
        self.assertEqual(len(rows), 2)

    def test_download_matches_the_filtered_page_exactly(self):
        """The whole promise of the feature, asserted end to end."""
        self.login_permitted()

        page = self.client.get(reverse("listening-home"), {"type": "episode"})
        page_names = [row["item"].name for row in page.context["rows"]]
        page_sessions = {row["item"].name: row["sessions"] for row in page.context["rows"]}

        download = self.client.get(self.url("csv"), {"type": "episode"})
        rows = list(csv.DictReader(StringIO(self.body(download))))

        self.assertEqual([row["name"] for row in rows], page_names)
        self.assertEqual({row["name"]: int(row["listens"]) for row in rows}, page_sessions)
        # And sessions, not observations: three polls across two sittings.
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.observation_count, 3)
        self.assertEqual(int(rows[0]["listens"]), 2)

    # --- the links on the page --------------------------------------------

    def test_download_buttons_submit_the_filter_form(self):
        """The buttons must live INSIDE the filter form, retargeted by formaction.

        This is the fix for a real bug: the downloads used to be server-rendered
        links carrying the *last submitted* query string, so choosing "Podcasts"
        in the dropdown and clicking CSV without first pressing Apply downloaded
        the entire library. As submit buttons of the form itself they send
        whatever the controls currently hold, so the file always matches the
        controls — submitted or not.
        """
        self.login_permitted()
        response = self.client.get(reverse("listening-home"))
        html = response.content.decode()

        form_start = html.index('<form class="listening-controls"')
        form_end = html.index("</form>", form_start)
        form_html = html[form_start:form_end]

        # Both buttons are inside the form...
        self.assertIn(f'formaction="{reverse("listening-download", args=["csv"])}"', form_html)
        self.assertIn(f'formaction="{reverse("listening-download", args=["markdown"])}"', form_html)
        # ...and are submit buttons, not anchors, so the form's values are sent.
        self.assertNotIn('<a href="/listening/download/', html)

        # The form is a GET form, so the controls arrive as query parameters.
        self.assertIn('method="get"', html[form_start:form_end + 7])

    def test_download_buttons_are_shown_even_with_no_matches(self):
        """They belong to the controls, not to the result set.

        Hiding them when the *submitted* filter matched nothing would mean the
        buttons vanish exactly when someone is mid-way through changing the
        filter to something that does match.
        """
        self.login_permitted()
        response = self.client.get(reverse("listening-home"), {"q": "definitely-no-match"})
        self.assertContains(response, 'formaction="/listening/download/csv/"')

    def test_download_of_an_empty_filter_is_a_header_only_file(self):
        """A filter matching nothing yields an empty table, not an error."""
        self.login_permitted()
        response = self.client.get(self.url("csv"), {"q": "definitely-no-match"})

        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(StringIO(self.body(response))))
        self.assertEqual(rows, [])
        self.assertIn("name,creator,type", self.body(response))
