"""Phase 1 tests for the `learning` app.

These deliberately test the CONSTRAINTS rather than the happy path. The scope's
load-bearing invariant (§2.6) is "a re-import must never destroy user data", and
every part of that rests on database constraints doing what the model file claims
they do. A comment asserting uniqueness is not a guarantee; a failing INSERT is.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from .models import (
    LearningPath, Note, NoteLink, NoteStatus, Question, StudyTask, Tag,
    TaskState, UserNote,
)


class PermissionAnchorTests(TestCase):
    """LearningAccess must create a permission and NOT a table."""

    def test_view_study_layer_permission_exists(self):
        self.assertTrue(
            Permission.objects.filter(
                codename="view_study_layer", content_type__app_label="learning"
            ).exists()
        )

    def test_anchor_creates_no_table(self):
        # managed = False means Django declares the model but builds no table.
        self.assertNotIn("learning_learningaccess", connection.introspection.table_names())

    def test_anchor_has_no_default_permissions(self):
        codenames = set(
            Permission.objects.filter(
                content_type__app_label="learning", content_type__model="learningaccess"
            ).values_list("codename", flat=True)
        )
        self.assertEqual(codenames, {"view_study_layer"})


class BaseData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.path = LearningPath.objects.create(slug="fiber-bundles", title="Fiber Bundles")
        cls.other = LearningPath.objects.create(slug="other-path", title="Other Path")
        cls.user = get_user_model().objects.create_user(username="anosh", password="x")
        cls.note = Note.objects.create(
            path=cls.path, kind=Note.Kind.CONCEPT, slug="fiber-bundle",
            title="Fiber Bundle", vault_filename="Fiber Bundle",
        )


class NoteConstraintTests(BaseData):

    def test_vault_filename_unique_within_a_path(self):
        """The §2.6 join key. If this ever stops holding, user notes attach to the wrong note."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            Note.objects.create(
                path=self.path, kind=Note.Kind.MODULE, slug="different-slug",
                title="Clash", vault_filename="Fiber Bundle",
            )

    def test_same_vault_filename_allowed_in_a_different_path(self):
        """Uniqueness is per-path, not global — a second learning path may reuse a name."""
        dup = Note.objects.create(
            path=self.other, kind=Note.Kind.CONCEPT, slug="fiber-bundle",
            title="Fiber Bundle", vault_filename="Fiber Bundle",
        )
        self.assertNotEqual(dup.pk, self.note.pk)

    def test_slug_unique_within_a_path(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Note.objects.create(
                path=self.path, kind=Note.Kind.MODULE, slug="fiber-bundle",
                title="Other title", vault_filename="Some Other File",
            )

    def test_concept_can_point_at_its_module(self):
        module = Note.objects.create(
            path=self.path, kind=Note.Kind.MODULE, slug="module-05",
            title="Module 05", vault_filename="Module 05 - Fiber Bundles",
        )
        self.note.module = module
        self.note.save()
        self.assertEqual(module.concepts.count(), 1)

    def test_deleting_a_module_nulls_the_concept_link_but_keeps_the_concept(self):
        module = Note.objects.create(
            path=self.path, kind=Note.Kind.MODULE, slug="module-08",
            title="Module 08", vault_filename="Module 08 - Connections",
        )
        self.note.module = module
        self.note.save()
        module.delete()
        self.note.refresh_from_db()
        self.assertIsNone(self.note.module)

    def test_deleting_a_path_cascades_to_its_notes(self):
        self.other.delete()
        self.assertTrue(Note.objects.filter(pk=self.note.pk).exists())
        self.path.delete()
        self.assertFalse(Note.objects.filter(pk=self.note.pk).exists())


class TagTests(BaseData):

    def test_tag_name_unique_per_path(self):
        Tag.objects.create(path=self.path, name="fiber-bundles/bundles",
                           leaf="bundles", slug="fiber-bundles-bundles")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Tag.objects.create(path=self.path, name="fiber-bundles/bundles",
                               leaf="bundles", slug="dupe")

    def test_tags_attach_to_notes(self):
        tag = Tag.objects.create(path=self.path, name="fiber-bundles/lie-theory",
                                 leaf="lie-theory", slug="fiber-bundles-lie-theory")
        self.note.tags.add(tag)
        self.assertEqual(tag.notes.count(), 1)


class NoteLinkTests(BaseData):

    def test_broken_link_is_representable(self):
        """§2.3 requires unresolvable links to be visible, so to_note must be nullable."""
        link = NoteLink.objects.create(
            from_note=self.note, to_note=None, raw_target="Does Not Exist",
        )
        self.assertIsNone(link.to_note)

    def test_deleting_the_target_leaves_the_link_row(self):
        target = Note.objects.create(
            path=self.path, kind=Note.Kind.CONCEPT, slug="target",
            title="Target", vault_filename="Target",
        )
        link = NoteLink.objects.create(from_note=self.note, to_note=target, raw_target="Target")
        target.delete()
        link.refresh_from_db()
        self.assertIsNone(link.to_note)
        self.assertEqual(link.raw_target, "Target")


class UserOwnedConstraintTests(BaseData):
    """These are the rows the importer must never touch (§2.6)."""

    def test_one_usernote_per_user_per_note(self):
        UserNote.objects.create(user=self.user, note=self.note, body_markdown="first")
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserNote.objects.create(user=self.user, note=self.note, body_markdown="second")

    def test_one_status_per_user_per_note(self):
        NoteStatus.objects.create(user=self.user, note=self.note)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NoteStatus.objects.create(user=self.user, note=self.note)

    def test_status_defaults_to_not_started(self):
        s = NoteStatus.objects.create(user=self.user, note=self.note)
        self.assertEqual(s.status, NoteStatus.Status.NOT_STARTED)

    def test_task_state_unique_per_user_note_and_hash(self):
        TaskState.objects.create(user=self.user, note=self.note, text_hash="a" * 64, done=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaskState.objects.create(user=self.user, note=self.note, text_hash="a" * 64)

    def test_two_different_tasks_on_one_note_both_hold_state(self):
        TaskState.objects.create(user=self.user, note=self.note, text_hash="a" * 64, done=True)
        TaskState.objects.create(user=self.user, note=self.note, text_hash="b" * 64, done=False)
        self.assertEqual(self.note.task_states.count(), 2)

    def test_task_state_is_not_tied_to_a_studytask_row(self):
        """The whole point of §2.6.2: replacing StudyTask rows must not clear tick state."""
        task = StudyTask.objects.create(
            note=self.note, kind=StudyTask.Kind.STUDY, text="Read the notes",
            text_hash="c" * 64, order=1,
        )
        TaskState.objects.create(user=self.user, note=self.note, text_hash="c" * 64, done=True)
        task.delete()  # simulate a re-import replacing vault-sourced rows
        state = TaskState.objects.get(user=self.user, note=self.note, text_hash="c" * 64)
        self.assertTrue(state.done)

    def test_question_can_be_standalone(self):
        q = Question.objects.create(user=self.user, note=None, title="Why is A a shadow?")
        self.assertIsNone(q.note)
        self.assertEqual(q.status, Question.Status.OPEN)
