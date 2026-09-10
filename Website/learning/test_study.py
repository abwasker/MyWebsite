"""Phase 4 tests — the private study layer.

Weighted toward the DOORS, not the happy path. The failure this phase is most
likely to ship is a write endpoint that forgets its permission check: the UI
hides the form, so the bug stays invisible until someone crafts a request.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from .models import (
    LearningPath, Note, NoteStatus, Question, StudyTask, TaskState, UserNote,
)
from .urls import PUBLIC_ROUTE_NAMES, urlpatterns
from .vault import task_hash


def grant(user):
    user.user_permissions.add(
        Permission.objects.get(codename="view_study_layer",
                               content_type__app_label="learning")
    )
    return get_user_model().objects.get(pk=user.pk)   # drop the perm cache


class StudyBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.path = LearningPath.objects.create(
            slug="demo", title="Demo", is_public=True, moc_filename="Demo MOC")
        cls.module = Note.objects.create(
            path=cls.path, kind=Note.Kind.MODULE, slug="m1", title="M1",
            vault_filename="M1", order=1, body_markdown="Body of M1.")
        cls.concept = Note.objects.create(
            path=cls.path, kind=Note.Kind.CONCEPT, slug="c1", title="C1",
            vault_filename="C1", body_markdown="Body of C1.")
        cls.task = StudyTask.objects.create(
            note=cls.module, kind=StudyTask.Kind.STUDY,
            text="Read the notes", text_hash=task_hash("Read the notes"), order=1)

        Users = get_user_model()
        cls.outsider = Users.objects.create_user("outsider", password="x")
        cls.student = grant(Users.objects.create_user("student", password="x"))
        cls.other = grant(Users.objects.create_user("other", password="x"))

    def study_url(self, note=None, name="learning-study", **extra):
        note = note or self.module
        segment = {"module": "modules", "concept": "concepts", "source": "sources"}[note.kind]
        return reverse(name, kwargs={"path_slug": "demo", "kind_segment": segment,
                                     "slug": note.slug, **extra})


# =====================================================================
# The doors
# =====================================================================


class GateTests(StudyBase):

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.study_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_authenticated_but_unpermitted_gets_403_not_a_login_redirect(self):
        """They are already past the login form; bouncing them there is a lie."""
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.study_url()).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("learning-dashboard", args=["demo"])).status_code, 403)

    def test_permitted_user_gets_the_page(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.study_url()).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("learning-dashboard", args=["demo"])).status_code, 200)

    def test_every_write_endpoint_rejects_an_unpermitted_post(self):
        """One endpoint at a time. This is the check most likely to be forgotten."""
        self.client.force_login(self.outsider)
        for name in ("learning-save-note", "learning-set-status",
                     "learning-save-tasks", "learning-ask-question"):
            with self.subTest(endpoint=name):
                self.assertEqual(self.client.post(self.study_url(name=name), {}).status_code, 403)

        question = Question.objects.create(user=self.student, note=self.module, title="Q")
        self.assertEqual(self.client.post(
            reverse("learning-answer-question", args=["demo", question.pk]),
            {"answer": "sneaky"}).status_code, 403)

    def test_every_non_public_route_is_gated(self):
        """Structural: walks the URLconf so a NEW ungated route fails the suite.

        A hand-written list only covers endpoints that existed when it was
        written; this covers the ones nobody has added yet.
        """
        ungated, checked = [], 0
        for pattern in urlpatterns:
            name = pattern.name
            if name in PUBLIC_ROUTE_NAMES:
                continue
            kwargs = {"path_slug": "demo"}
            if "kind_segment" in pattern.pattern.regex.groupindex:
                kwargs.update(kind_segment="modules", slug="m1")
            if "question_id" in str(pattern.pattern):
                kwargs = {"path_slug": "demo", "question_id": 1}
            url = reverse(name, kwargs=kwargs)
            checked += 1
            get, post = self.client.get(url), self.client.post(url, {})
            # Anonymous must never get a 200 from a private route.
            if get.status_code == 200 or post.status_code == 200:
                ungated.append(name)
        self.assertEqual(ungated, [], f"ungated private routes: {ungated}")
        # Guard against a VACUOUS pass: if the loop stopped reversing URLs this
        # test would report success having checked nothing.
        self.assertGreaterEqual(checked, 6, "the gating test checked almost nothing")

    def test_unpublished_path_is_404_even_with_permission(self):
        self.path.is_public = False
        self.path.save()
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.study_url()).status_code, 404)


class PublicPagesUnchangedTests(StudyBase):
    """Phase 3's guarantee must survive Phase 4."""

    def test_no_user_owned_data_in_a_public_context(self):
        UserNote.objects.create(user=self.student, note=self.concept, body_markdown="secret")
        response = self.client.get(reverse("learning-concept", args=["demo", "c1"]))
        for key in ("user_note", "user_note_html", "status_form", "questions",
                    "note_form", "task_states"):
            self.assertNotIn(key, response.context)

    def test_a_permitted_users_notes_never_appear_on_the_public_page(self):
        UserNote.objects.create(user=self.student, note=self.concept,
                                body_markdown="my private thinking")
        self.client.force_login(self.student)
        response = self.client.get(reverse("learning-concept", args=["demo", "c1"]))
        self.assertNotContains(response, "my private thinking")

    def test_study_link_shown_only_to_permitted_users(self):
        public = reverse("learning-concept", args=["demo", "c1"])
        self.assertNotContains(self.client.get(public), "Study this note")
        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get(public), "Study this note")
        self.client.force_login(self.student)
        self.assertContains(self.client.get(public), "Study this note")


class UserIsolationTests(StudyBase):
    """Two users, despite there being one in reality. Retrofitting this is not free."""

    def test_one_user_cannot_see_anothers_notes(self):
        UserNote.objects.create(user=self.other, note=self.module, body_markdown="others note")
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(self.study_url()), "others note")

    def test_one_user_cannot_answer_anothers_question(self):
        question = Question.objects.create(user=self.other, note=self.module, title="Theirs")
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("learning-answer-question", args=["demo", question.pk]),
            {"answer": "hijacked"})
        self.assertEqual(response.status_code, 404)
        question.refresh_from_db()
        self.assertEqual(question.answer, "")

    def test_saving_a_note_does_not_overwrite_anothers(self):
        UserNote.objects.create(user=self.other, note=self.module, body_markdown="theirs")
        self.client.force_login(self.student)
        self.client.post(self.study_url(name="learning-save-note"), {"body_markdown": "mine"})
        self.assertEqual(
            UserNote.objects.get(user=self.other, note=self.module).body_markdown, "theirs")
        self.assertEqual(
            UserNote.objects.get(user=self.student, note=self.module).body_markdown, "mine")


# =====================================================================
# Behaviour
# =====================================================================


class StudyWriteTests(StudyBase):

    def setUp(self):
        self.client.force_login(self.student)

    def test_saving_notes_then_reloading_shows_them_rendered(self):
        self.client.post(self.study_url(name="learning-save-note"),
                         {"body_markdown": "A thought about $S^2$ and [[C1]]."})
        response = self.client.get(self.study_url())
        self.assertContains(response, "$S^2$")                       # math preserved for KaTeX
        self.assertContains(response, 'href="/learning/demo/concepts/c1/"')  # wikilink resolved

    def test_status_row_is_created_lazily_and_absence_means_not_started(self):
        self.assertFalse(NoteStatus.objects.filter(note=self.module).exists())
        response = self.client.get(self.study_url())
        self.assertEqual(response.context["current_status"], "not-started")
        self.client.post(self.study_url(name="learning-set-status"), {"status": "reading"})
        self.assertEqual(NoteStatus.objects.get(user=self.student, note=self.module).status,
                         "reading")

    def test_ticking_a_task_records_state_against_its_hash(self):
        self.client.post(self.study_url(name="learning-save-tasks"),
                         {"task": [self.task.text_hash]})
        state = TaskState.objects.get(user=self.student, note=self.module,
                                      text_hash=self.task.text_hash)
        self.assertTrue(state.done)
        self.assertIsNotNone(state.done_at)

    def test_unticking_clears_the_timestamp(self):
        self.client.post(self.study_url(name="learning-save-tasks"),
                         {"task": [self.task.text_hash]})
        self.client.post(self.study_url(name="learning-save-tasks"), {})
        state = TaskState.objects.get(user=self.student, note=self.module)
        self.assertFalse(state.done)
        self.assertIsNone(state.done_at)

    def test_a_crafted_hash_cannot_invent_task_state(self):
        """Only hashes belonging to this note are honoured."""
        self.client.post(self.study_url(name="learning-save-tasks"), {"task": ["deadbeef" * 8]})
        self.assertFalse(TaskState.objects.filter(text_hash="deadbeef" * 8).exists())

    def test_tick_state_survives_the_importer_replacing_task_rows(self):
        """End-to-end version of the §2.6.2 guarantee."""
        self.client.post(self.study_url(name="learning-save-tasks"),
                         {"task": [self.task.text_hash]})
        self.task.delete()
        StudyTask.objects.create(note=self.module, kind=StudyTask.Kind.STUDY,
                                 text="Read the notes",
                                 text_hash=task_hash("Read the notes"), order=1)
        response = self.client.get(self.study_url())
        self.assertTrue(response.context["tasks"][0].is_done)

    def test_asking_and_answering_a_question(self):
        self.client.post(self.study_url(name="learning-ask-question"),
                         {"title": "Why is A a shadow?", "body": ""})
        question = Question.objects.get(user=self.student, note=self.module)
        self.assertEqual(question.status, "open")
        self.client.post(reverse("learning-answer-question", args=["demo", question.pk]),
                         {"answer": "Because it is a pullback."})
        question.refresh_from_db()
        self.assertEqual(question.status, "answered")

    def test_clearing_an_answer_reopens_the_question(self):
        question = Question.objects.create(user=self.student, note=self.module,
                                           title="Q", answer="a",
                                           status=Question.Status.ANSWERED)
        self.client.post(reverse("learning-answer-question", args=["demo", question.pk]),
                         {"answer": "   "})
        question.refresh_from_db()
        self.assertEqual(question.status, "open")

    def test_writes_redirect_back_to_the_study_view(self):
        response = self.client.post(self.study_url(name="learning-save-note"),
                                    {"body_markdown": "x"})
        self.assertRedirects(response, self.study_url())

    def test_get_on_a_write_endpoint_is_rejected(self):
        self.assertEqual(
            self.client.get(self.study_url(name="learning-save-note")).status_code, 405)


class DashboardTests(StudyBase):

    def setUp(self):
        self.client.force_login(self.student)

    def test_dashboard_reports_progress_from_status_rows(self):
        response = self.client.get(reverse("learning-dashboard", args=["demo"]))
        self.assertEqual(response.context["progress_total"], 2)
        self.assertEqual(response.context["progress_done"], 0)
        NoteStatus.objects.create(user=self.student, note=self.concept,
                                  status=NoteStatus.Status.DONE)
        response = self.client.get(reverse("learning-dashboard", args=["demo"]))
        self.assertEqual(response.context["progress_done"], 1)
        self.assertEqual(response.context["progress_pct"], 50)

    def test_completed_concepts_move_out_of_the_open_list(self):
        NoteStatus.objects.create(user=self.student, note=self.concept,
                                  status=NoteStatus.Status.DONE)
        response = self.client.get(reverse("learning-dashboard", args=["demo"]))
        self.assertEqual([c.pk for c in response.context["concepts_done"]], [self.concept.pk])
        self.assertEqual(response.context["concepts_open"], [])

    def test_open_tasks_exclude_ticked_ones(self):
        response = self.client.get(reverse("learning-dashboard", args=["demo"]))
        self.assertEqual(len(response.context["open_tasks"]), 1)
        TaskState.objects.create(user=self.student, note=self.module,
                                 text_hash=self.task.text_hash, done=True)
        response = self.client.get(reverse("learning-dashboard", args=["demo"]))
        self.assertEqual(response.context["open_tasks"], [])

    def test_dashboard_shows_only_this_users_questions(self):
        Question.objects.create(user=self.other, note=self.module, title="Theirs")
        Question.objects.create(user=self.student, note=self.module, title="Mine")
        response = self.client.get(reverse("learning-dashboard", args=["demo"]))
        titles = [q.title for q in response.context["open_questions"]]
        self.assertEqual(titles, ["Mine"])
