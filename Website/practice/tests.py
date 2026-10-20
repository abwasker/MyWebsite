"""Tests for the practice app (scope: [[Music Interval Learning App]] Phase 1).

Written before any UI, because the parts worth guarding are the ones with no
visible symptom when they break: a batch that stores silently shortened, a
duplicate that double-counts, one user's rows reachable by another.

⚠️ Every test class clears the cache in setUp. Django falls back to LocMemCache
when CACHES is unset, and that cache is shared by every test in the process — so
rate-limit counters leak between tests and make failures depend on test order.
"""

import json
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import PracticeAttempt
from .views import MAX_ATTEMPTS_PER_DAY, MAX_BATCH, MAX_BODY_BYTES, MAX_REQUESTS_PER_MINUTE

User = get_user_model()


def attempt(mode="notes", steps=2, correct=True, client_id=None):
    """One well-formed attempt, for tests that vary a single field."""
    return {
        "client_id": str(client_id or uuid.uuid4()),
        "mode": mode,
        "interval_steps": steps,
        "correct": correct,
    }


class PracticeTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.page = reverse("interval-ladder")
        self.attempts_url = reverse("interval-ladder-attempts")
        self.progress_url = reverse("interval-ladder-progress")

    def post(self, payload, raw=None):
        body = raw if raw is not None else json.dumps(payload)
        return self.client.post(self.attempts_url, data=body, content_type="application/json")


# --- Access: the page is public, the endpoints are not ---


class AccessTests(PracticeTestCase):
    """The page and the endpoints have deliberately different gates."""

    def test_the_page_is_public(self):
        """Anyone may practise; only saving needs an account (scope §2.1)."""
        self.assertEqual(self.client.get(self.page).status_code, 200)

    def test_the_page_tells_anonymous_visitors_their_practice_is_not_saved(self):
        """A silent non-save would look identical to a save that worked."""
        out = self.client.get(self.page).content.decode()
        self.assertIn("not saved", out)

    def test_the_page_does_not_invite_anonymous_visitors_to_sign_in(self):
        """🔴 Scope §2.1: public signup is closed, so a 'log in to save' CTA sends
        almost every visitor somewhere that cannot help them."""
        out = self.client.get(self.page).content.decode().lower()
        for bait in ("log in to save", "sign in to save", "sign up"):
            self.assertNotIn(bait, out)

    def test_both_endpoints_refuse_anonymous_callers(self):
        """Each carries its own gate — a second door needs its own lock."""
        self.assertEqual(self.post({"attempts": [attempt()]}).status_code, 403)
        self.assertEqual(self.client.get(self.progress_url).status_code, 403)

    def test_refusal_is_json_not_a_redirect_to_html(self):
        """⚠️ login_required would 302 to an HTML page, which a fetch() parses as
        JSON and fails on — so 'not signed in' would surface as a syntax error."""
        response = self.post({"attempts": [attempt()]})
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", json.loads(response.content))

    def test_anonymous_posts_store_nothing(self):
        self.post({"attempts": [attempt()]})
        self.assertEqual(PracticeAttempt.objects.count(), 0)


# --- Saving ---


class SaveAttemptsTests(PracticeTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("learner", password="practice-pw-1")
        self.client.force_login(self.user)

    def test_a_batch_is_stored(self):
        response = self.post({"attempts": [attempt(steps=3), attempt(steps=-4, correct=False)]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"saved": 2, "received": 2})
        self.assertEqual(PracticeAttempt.objects.filter(user=self.user).count(), 2)

    def test_resending_the_same_batch_stores_nothing_new(self):
        """🔴 The point of client_id. A retry after a lost response must not
        double-count — the artifact's additive counters silently did."""
        batch = [attempt(client_id=uuid.uuid4()) for _ in range(3)]
        first = self.post({"attempts": batch})
        second = self.post({"attempts": batch})

        self.assertEqual(json.loads(first.content)["saved"], 3)
        self.assertEqual(json.loads(second.content)["saved"], 0)
        self.assertEqual(json.loads(second.content)["received"], 3)
        self.assertEqual(PracticeAttempt.objects.filter(user=self.user).count(), 3)

    def test_two_users_may_share_a_client_id(self):
        """Uniqueness is scoped per user; a global index would make one user's
        write fail because of another's."""
        shared = uuid.uuid4()
        self.post({"attempts": [attempt(client_id=shared)]})

        other = User.objects.create_user("other", password="practice-pw-2")
        self.client.force_login(other)
        response = self.post({"attempts": [attempt(client_id=shared)]})

        self.assertEqual(json.loads(response.content)["saved"], 1)
        self.assertEqual(PracticeAttempt.objects.count(), 2)

    def test_an_empty_batch_is_accepted_and_stores_nothing(self):
        response = self.post({"attempts": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PracticeAttempt.objects.count(), 0)

    def test_a_get_on_the_save_endpoint_is_not_allowed(self):
        self.assertEqual(self.client.get(self.attempts_url).status_code, 405)


# --- Validation: a bad batch is rejected WHOLE ---


class ValidationTests(PracticeTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("picky", password="practice-pw-3")
        self.client.force_login(self.user)

    def assertRejected(self, payload, raw=None, status=400):
        response = self.post(payload, raw=raw)
        self.assertEqual(response.status_code, status, response.content[:200])
        self.assertIn("error", json.loads(response.content))
        # 🔴 The guarantee that matters: nothing from a rejected batch is kept.
        self.assertEqual(PracticeAttempt.objects.count(), 0)

    def test_one_bad_entry_rejects_the_whole_batch(self):
        """All-or-nothing. A silently shortened save is indistinguishable from a
        successful one, which is the failure shape to avoid."""
        self.assertRejected({"attempts": [attempt(), attempt(mode="humming"), attempt()]})

    def test_unknown_mode_is_rejected(self):
        self.assertRejected({"attempts": [attempt(mode="whistling")]})

    def test_interval_out_of_range_is_rejected(self):
        self.assertRejected({"attempts": [attempt(steps=8)]})
        self.assertRejected({"attempts": [attempt(steps=-8)]})

    def test_a_boolean_is_not_an_interval(self):
        """bool subclasses int in Python, so True would otherwise pass as 1."""
        self.assertRejected({"attempts": [attempt(steps=True)]})

    def test_correct_must_be_a_real_boolean(self):
        """Otherwise "no" and 0 would disagree about what they mean."""
        self.assertRejected({"attempts": [attempt(correct="true")]})
        self.assertRejected({"attempts": [attempt(correct=1)]})

    def test_a_malformed_client_id_is_rejected(self):
        self.assertRejected({"attempts": [attempt(client_id="not-a-uuid")]})

    def test_a_missing_field_is_rejected(self):
        broken = attempt()
        del broken["correct"]
        self.assertRejected({"attempts": [broken]})

    def test_attempts_must_be_a_list(self):
        self.assertRejected({"attempts": attempt()})
        self.assertRejected({"nope": []})

    def test_a_non_json_body_is_rejected(self):
        self.assertRejected(None, raw="this is not json")

    def test_an_oversized_batch_is_rejected(self):
        self.assertRejected({"attempts": [attempt() for _ in range(MAX_BATCH + 1)]})

    def test_an_oversized_body_is_rejected_before_parsing(self):
        """🔴 The size guard a JSON blob would have needed. Here it is a backstop,
        because a fixed-size row has no size vector of its own."""
        padding = "x" * (MAX_BODY_BYTES + 1)
        self.assertRejected(None, raw=json.dumps({"attempts": [], "pad": padding}), status=413)


# --- Rate limiting ---


class RateLimitTests(PracticeTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("eager", password="practice-pw-4")
        self.client.force_login(self.user)

    def test_too_many_requests_in_a_minute_is_refused(self):
        for _ in range(MAX_REQUESTS_PER_MINUTE):
            self.assertEqual(self.post({"attempts": [attempt()]}).status_code, 200)
        self.assertEqual(self.post({"attempts": [attempt()]}).status_code, 429)

    def test_a_batch_that_would_exceed_the_daily_cap_is_refused(self):
        """Counted in attempts, not requests — the two bound different things."""
        today = timezone.now().date().isoformat()
        cache.set(f"practice-attempts-daily-{self.user.pk}-{today}", MAX_ATTEMPTS_PER_DAY - 1, timeout=60)
        response = self.post({"attempts": [attempt(), attempt()]})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(PracticeAttempt.objects.count(), 0)


# --- Progress ---


class ProgressTests(PracticeTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("tracked", password="practice-pw-5")
        self.client.force_login(self.user)

    def _seed(self, user, rows):
        PracticeAttempt.objects.bulk_create(
            [
                PracticeAttempt(
                    user=user,
                    tool=PracticeAttempt.DEFAULT_TOOL,
                    mode=mode,
                    interval_steps=steps,
                    correct=correct,
                    client_id=uuid.uuid4(),
                )
                for mode, steps, correct in rows
            ]
        )

    def test_totals_and_per_interval_counts(self):
        self._seed(
            self.user,
            [("notes", 3, True), ("notes", 3, False), ("intervals", -2, True)],
        )
        data = json.loads(self.client.get(self.progress_url).content)

        self.assertEqual(data["total"], {"n": 3, "c": 2})
        by = {(r["mode"], r["interval_steps"]): (r["c"], r["n"]) for r in data["by_interval"]}
        self.assertEqual(by[("notes", 3)], (1, 2))
        self.assertEqual(by[("intervals", -2)], (1, 1))

    def test_progress_is_empty_rather_than_absent_for_a_new_user(self):
        """An empty result and a broken endpoint must not look the same."""
        data = json.loads(self.client.get(self.progress_url).content)
        self.assertEqual(data["total"], {"n": 0, "c": 0})
        self.assertEqual(data["by_interval"], [])

    def test_one_users_progress_is_invisible_to_another(self):
        other = User.objects.create_user("stranger", password="practice-pw-6")
        self._seed(other, [("notes", 5, True)] * 4)
        self._seed(self.user, [("notes", 1, True)])

        data = json.loads(self.client.get(self.progress_url).content)
        self.assertEqual(data["total"]["n"], 1)


# --- The database backstop ---


class ModelConstraintTests(TestCase):
    """Python validation is the real guard; these prove the database agrees.

    ⚠️ It matters because the API writes with bulk_create, which does not call
    full_clean() — so the field validators alone would not stop a bad row.
    """

    def setUp(self):
        self.user = User.objects.create_user("dbcheck", password="practice-pw-7")

    def _row(self, **kwargs):
        defaults = {
            "user": self.user,
            "tool": PracticeAttempt.DEFAULT_TOOL,
            "mode": "notes",
            "interval_steps": 2,
            "correct": True,
            "client_id": uuid.uuid4(),
        }
        return PracticeAttempt(**{**defaults, **kwargs})

    def test_an_out_of_range_interval_is_refused_by_the_database(self):
        for steps in (8, -8, 99):
            with self.subTest(steps=steps), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._row(interval_steps=steps).save()

    def test_a_duplicate_client_id_for_one_user_is_refused(self):
        shared = uuid.uuid4()
        self._row(client_id=shared).save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._row(client_id=shared).save()
