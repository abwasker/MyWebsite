"""Phase 2 tests — the importer and the vault parser.

⚠️ EVERY TEST BUILDS ITS OWN SYNTHETIC VAULT IN A TEMP DIRECTORY.
Nothing here reads `G:\\My Drive`. That path does not exist on the home desktop or
on the server, and a suite that depends on one machine's Google Drive is not a
suite. The real corpus is exercised by running the command, not by CI.

The headline test is `test_user_rows_survive_a_reimport` — scope §2.6, the one
invariant whose failure is silent and unrecoverable.
"""
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import vault
from .importer import run_import
from .models import (
    LearningPath, Note, NoteLink, NoteStatus, StudyTask, Tag, TaskState, UserNote,
)

CONCEPT = """---
type: concept
status: not-started
module: "[[Module 01 - Basics]]"
tags: [demo/alpha, demo/beta]
---

# Fiber Bundle

A bundle links to [[Local Trivialization]] and [[Module 01 - Basics]].
Set-builder survives: $\\{x : x \\in M\\}$.

## My Notes

- my private thinking

## Questions

- [ ] #question
"""

MODULE = """---
type: module
status: not-started
part: I - Refreshers
tags: [demo/module]
---

# Module 01 - Basics

[[Demo MOC]] · Next: [[Fiber Bundle]]

## Study Tasks

- [ ] #study Read the concept notes
- [ ] #study Do the exercise

## My Notes

-

## Questions

- [ ] #question
"""

SOURCE = """---
type: source
tags: [demo/source]
---

# A Book

Relevant to [[Fiber Bundle]]. The plan lives in [[Demo MOC]].

## Study Tasks

- [ ] #study Link my paper

## My Notes

-
"""

TRIVIALIZATION = """---
type: concept
tags: [demo/alpha]
---

# Local Trivialization

The charts of bundle theory.

## My Notes

-

## Questions

- [ ] #question
"""

MOC = """---
type: moc
tags: [demo]
---

# Demo MOC

```dataview
TABLE status FROM "demo"
```
"""

README = """---
type: info
tags: [demo]
---

# Not importable
"""


def build_vault(root, files):
    for name, text in files.items():
        (root / f"{name}.md").write_text(text, encoding="utf-8")


DEFAULT_FILES = {
    "Fiber Bundle": CONCEPT,
    "Module 01 - Basics": MODULE,
    "A Book": SOURCE,
    "Local Trivialization": TRIVIALIZATION,
    "Demo MOC": MOC,
    "README - Questions": README,
}


class VaultDirMixin:
    def make_vault(self, files=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        build_vault(root, DEFAULT_FILES if files is None else files)
        return root


# =====================================================================
# Pure parser (no database)
# =====================================================================


class VaultParserTests(TestCase):

    def test_only_importable_types_are_parsed(self):
        self.assertIsNone(vault.parse_note("Demo MOC", MOC))
        self.assertIsNone(vault.parse_note("README", README))
        self.assertIsNotNone(vault.parse_note("Fiber Bundle", CONCEPT))

    def test_personal_sections_are_stripped(self):
        note = vault.parse_note("Fiber Bundle", CONCEPT)
        self.assertNotIn("## My Notes", note.body_markdown)
        self.assertNotIn("my private thinking", note.body_markdown)
        self.assertIn("A bundle links to", note.body_markdown)

    def test_stripping_is_by_heading_not_by_position(self):
        """A section merely CONTAINING the word must survive — real case: Module 04."""
        text = CONCEPT.replace("## Questions", "## Questions to hold while reading")
        note = vault.parse_note("X", text)
        self.assertIn("## Questions to hold while reading", note.body_markdown)

    def test_source_without_a_questions_section_still_strips(self):
        """All three source notes lack `## Questions`; stripping must not depend on it.
        (`## Study Tasks` is also gone now — it is extracted into rows instead.)"""
        note = vault.parse_note("A Book", SOURCE)
        self.assertNotIn("## My Notes", note.body_markdown)
        self.assertIn("Relevant to", note.body_markdown)
        self.assertEqual(len(note.tasks), 1)

    def test_tag_only_task_stubs_are_skipped(self):
        note = vault.parse_note("Fiber Bundle", CONCEPT)
        self.assertEqual(note.tasks, [])

    def test_real_tasks_are_kept_with_kind(self):
        note = vault.parse_note("Module 01 - Basics", MODULE)
        self.assertEqual(len(note.tasks), 2)
        self.assertEqual({t.kind for t in note.tasks}, {"study"})

    def test_task_hash_ignores_tags_and_whitespace(self):
        self.assertEqual(
            vault.task_hash("#study  Read   the   notes"),
            vault.task_hash("Read the notes"),
        )

    def test_module_frontmatter_wikilink_is_unwrapped(self):
        note = vault.parse_note("Fiber Bundle", CONCEPT)
        self.assertEqual(note.module_target, "Module 01 - Basics")

    def test_piped_link_keeps_display_text(self):
        links = vault.extract_links("see [[Target Note|the target]] here")
        self.assertEqual(links[0].raw_target, "Target Note")
        self.assertEqual(links[0].display_text, "the target")

    def test_anchors_and_block_refs_are_stripped_from_targets(self):
        self.assertEqual(vault.extract_links("[[A Note#Section]]")[0].raw_target, "A Note")
        self.assertEqual(vault.extract_links("[[A Note^abc123]]")[0].raw_target, "A Note")

    def test_image_embeds_are_not_treated_as_links(self):
        self.assertEqual(vault.extract_links("![[picture.png]]"), [])

    def test_module_order_comes_from_the_filename_number(self):
        self.assertEqual(vault.derive_order("Module 07 - Principal Bundles"), 7)
        self.assertEqual(vault.derive_order("Fiber Bundle"), 0)


# =====================================================================
# Importer
# =====================================================================


class ImporterTests(VaultDirMixin, TestCase):

    def test_imports_only_the_three_note_kinds(self):
        report = run_import("demo", self.make_vault())
        self.assertEqual(len(report.created), 4)
        self.assertEqual(sorted(report.skipped_files), ["Demo MOC", "README - Questions"])
        self.assertEqual(Note.objects.count(), 4)

    def test_moc_filename_is_recorded_on_the_path(self):
        run_import("demo", self.make_vault())
        self.assertEqual(LearningPath.objects.get(slug="demo").moc_filename, "Demo MOC")

    def test_links_to_the_moc_are_not_reported_as_broken(self):
        """A MOC reference in PROSE (module nav lines are stripped before this point)."""
        report = run_import("demo", self.make_vault())
        self.assertEqual(report.broken_links, [])
        self.assertEqual(report.moc_links, 1)

    def test_genuinely_broken_link_is_reported(self):
        files = dict(DEFAULT_FILES)
        files["A Book"] = SOURCE.replace("[[Fiber Bundle]]", "[[No Such Note]]")
        report = run_import("demo", self.make_vault(files))
        self.assertEqual(report.broken_links, [("A Book", "No Such Note")])

    def test_module_fk_resolves_across_the_two_passes(self):
        run_import("demo", self.make_vault())
        concept = Note.objects.get(vault_filename="Fiber Bundle")
        self.assertEqual(concept.module.vault_filename, "Module 01 - Basics")

    def test_tags_are_created_and_attached(self):
        run_import("demo", self.make_vault())
        self.assertEqual(Tag.objects.count(), 4)  # demo/alpha, beta, module, source
        concept = Note.objects.get(vault_filename="Fiber Bundle")
        self.assertEqual({t.name for t in concept.tags.all()}, {"demo/alpha", "demo/beta"})

    def test_math_braces_are_preserved_verbatim_in_the_body(self):
        """The importer must not touch math — protection happens at render time."""
        run_import("demo", self.make_vault())
        body = Note.objects.get(vault_filename="Fiber Bundle").body_markdown
        self.assertIn(r"$\{x : x \in M\}$", body)

    # ---- idempotency -----------------------------------------------------

    def test_second_run_changes_nothing(self):
        root = self.make_vault()
        run_import("demo", root)
        report = run_import("demo", root)
        self.assertEqual(report.created, [])
        self.assertEqual(report.updated, [])
        self.assertEqual(len(report.unchanged), 4)
        self.assertFalse(report.changed)

    def test_touching_a_file_without_editing_it_is_not_a_change(self):
        """Change detection is content-based; mtime must not count (scope §2.b.5)."""
        root = self.make_vault()
        run_import("demo", root)
        f = root / "Fiber Bundle.md"
        f.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")  # bumps mtime only
        report = run_import("demo", root)
        self.assertEqual(report.updated, [])

    def test_editing_content_is_detected(self):
        root = self.make_vault()
        run_import("demo", root)
        f = root / "Fiber Bundle.md"
        f.write_text(f.read_text(encoding="utf-8").replace("A bundle links",
                                                           "A fibre bundle links"), encoding="utf-8")
        report = run_import("demo", root)
        self.assertEqual(report.updated, ["Fiber Bundle"])

    # ---- dry run ---------------------------------------------------------

    def test_dry_run_writes_absolutely_nothing(self):
        report = run_import("demo", self.make_vault(), dry_run=True)
        self.assertEqual(len(report.created), 4)
        self.assertEqual(Note.objects.count(), 0)
        self.assertEqual(Tag.objects.count(), 0)
        self.assertEqual(LearningPath.objects.count(), 0)  # incl. the path row itself

    # ---- archive / restore -----------------------------------------------

    def test_vanished_file_is_archived_not_deleted(self):
        root = self.make_vault()
        run_import("demo", root)
        (root / "Local Trivialization.md").unlink()
        report = run_import("demo", root)
        self.assertEqual(report.archived, ["Local Trivialization"])
        note = Note.objects.get(vault_filename="Local Trivialization")
        self.assertTrue(note.is_archived)

    def test_returning_file_is_restored(self):
        root = self.make_vault()
        run_import("demo", root)
        target = root / "Local Trivialization.md"
        text = target.read_text(encoding="utf-8")
        target.unlink()
        run_import("demo", root)
        target.write_text(text, encoding="utf-8")
        report = run_import("demo", root)
        self.assertEqual(report.restored, ["Local Trivialization"])
        self.assertFalse(Note.objects.get(vault_filename="Local Trivialization").is_archived)

    # ---- slug stability --------------------------------------------------

    def test_slug_is_never_recomputed_and_drift_is_reported(self):
        """The slug is in the public URL; a retitle must not silently break a link."""
        root = self.make_vault()
        run_import("demo", root)
        note = Note.objects.get(vault_filename="Fiber Bundle")
        note.slug = "legacy-url"
        note.save(update_fields=["slug"])
        report = run_import("demo", root)
        note.refresh_from_db()
        self.assertEqual(note.slug, "legacy-url")
        self.assertEqual(report.slug_conflicts,
                         [("Fiber Bundle", "legacy-url", "fiber-bundle")])

    # ---- THE headline invariant (scope §2.6) -----------------------------

    def test_user_rows_survive_a_reimport(self):
        root = self.make_vault()
        run_import("demo", root)

        user = get_user_model().objects.create_user(username="anosh", password="x")
        concept = Note.objects.get(vault_filename="Fiber Bundle")
        module = Note.objects.get(vault_filename="Module 01 - Basics")
        task_hash = StudyTask.objects.filter(note=module).first().text_hash

        UserNote.objects.create(user=user, note=concept, body_markdown="irreplaceable thinking")
        NoteStatus.objects.create(user=user, note=concept, status=NoteStatus.Status.REVIEW)
        TaskState.objects.create(user=user, note=module, text_hash=task_hash, done=True)

        # Edit every file so the importer genuinely rewrites notes, tasks and links.
        for f in root.glob("*.md"):
            f.write_text(f.read_text(encoding="utf-8") + "\n\nAppended line.\n", encoding="utf-8")
        run_import("demo", root)

        self.assertEqual(UserNote.objects.get(user=user, note=concept).body_markdown,
                         "irreplaceable thinking")
        self.assertEqual(NoteStatus.objects.get(user=user, note=concept).status, "review")
        state = TaskState.objects.get(user=user, note=module, text_hash=task_hash)
        self.assertTrue(state.done)

    def test_tick_state_survives_studytask_rows_being_replaced(self):
        """StudyTask rows are replaced wholesale; TaskState joins on text_hash, not row id."""
        root = self.make_vault()
        run_import("demo", root)
        user = get_user_model().objects.create_user(username="a", password="x")
        module = Note.objects.get(vault_filename="Module 01 - Basics")
        keep = vault.task_hash("Read the concept notes")
        TaskState.objects.create(user=user, note=module, text_hash=keep, done=True)

        # Reorder the tasks in the file — same texts, different rows and order.
        f = root / "Module 01 - Basics.md"
        f.write_text(f.read_text(encoding="utf-8").replace(
            "- [ ] #study Read the concept notes\n- [ ] #study Do the exercise",
            "- [ ] #study Do the exercise\n- [ ] #study Read the concept notes",
        ), encoding="utf-8")
        run_import("demo", root)

        self.assertTrue(TaskState.objects.get(user=user, note=module, text_hash=keep).done)

    def test_importer_never_creates_or_deletes_user_rows(self):
        root = self.make_vault()
        run_import("demo", root)
        run_import("demo", root)
        self.assertEqual(UserNote.objects.count(), 0)
        self.assertEqual(NoteStatus.objects.count(), 0)
        self.assertEqual(TaskState.objects.count(), 0)


class TitleAndNavStrippingTests(TestCase):
    """The H1 is the display title; the module nav line duplicates our own chrome."""

    def test_h1_becomes_the_title_and_leaves_the_body(self):
        note = vault.parse_note("Mobius Band as a Bundle", (
            "---\ntype: concept\n---\n\n# Möbius Band as a Bundle\n\nThe minimal example.\n"
        ))
        self.assertEqual(note.title, "Möbius Band as a Bundle")
        self.assertNotIn("#", note.body_markdown)
        self.assertIn("The minimal example.", note.body_markdown)

    def test_title_falls_back_to_the_filename_when_there_is_no_h1(self):
        note = vault.parse_note("No Heading", "---\ntype: concept\n---\n\nJust prose.\n")
        self.assertEqual(note.title, "No Heading")

    def test_an_h1_after_prose_is_left_alone(self):
        """Only a LEADING H1 is a title; one further down is real content."""
        note = vault.parse_note("X", "---\ntype: concept\n---\n\nIntro.\n\n# A Later Heading\n")
        self.assertEqual(note.title, "X")
        self.assertIn("# A Later Heading", note.body_markdown)

    def test_nav_line_with_both_prev_and_next_is_stripped(self):
        """The case an earlier version missed: it allowed only ONE prev/next label,
        so it stripped the two end modules and silently left the other nine."""
        note = vault.parse_note("Module 05", (
            "---\ntype: module\n---\n\n# Module 05\n\n"
            "[[Demo MOC]] · Prev: [[Module 04]] · Next: [[Module 06]]\n\nReal content.\n"
        ))
        self.assertNotIn("Prev:", note.body_markdown)
        self.assertNotIn("Next:", note.body_markdown)
        self.assertIn("Real content.", note.body_markdown)

    def test_nav_line_with_only_next_is_stripped(self):
        note = vault.parse_note("Module 01", (
            "---\ntype: module\n---\n\n# Module 01\n\n"
            "[[Demo MOC]] · Next: [[Module 02]]\n\nReal content.\n"
        ))
        self.assertNotIn("Next:", note.body_markdown)

    def test_a_line_with_real_prose_and_links_is_kept(self):
        """Conservative by design — this must not eat authored content."""
        body = "See [[Fiber Bundle]] and [[Principal Bundle]] for the definitions."
        note = vault.parse_note("X", f"---\ntype: concept\n---\n\n# X\n\n{body}\n")
        self.assertIn("for the definitions", note.body_markdown)

    def test_links_in_a_stripped_nav_line_do_not_become_notelinks(self):
        note = vault.parse_note("Module 05", (
            "---\ntype: module\n---\n\n# Module 05\n\n"
            "[[Demo MOC]] · Prev: [[Module 04]] · Next: [[Module 06]]\n\n"
            "Body links to [[Fiber Bundle]].\n"
        ))
        self.assertEqual([l.raw_target for l in note.links], ["Fiber Bundle"])


class StudyTaskPresentationTests(TestCase):
    """Tasks are extracted into rows, so the raw Obsidian syntax must not also
    render in the body — and the stored text must be clean enough to display."""

    def test_study_tasks_section_is_stripped_from_the_body(self):
        note = vault.parse_note("Module 01 - Basics", MODULE)
        self.assertNotIn("## Study Tasks", note.body_markdown)
        self.assertNotIn("[ ]", note.body_markdown)

    def test_task_text_has_no_tag_or_checkbox_syntax(self):
        note = vault.parse_note("Module 01 - Basics", MODULE)
        for task in note.tasks:
            self.assertNotIn("#", task.text)
            self.assertNotIn("[ ]", task.text)
        self.assertEqual(note.tasks[0].text, "Read the concept notes")

    def test_task_text_matches_its_own_hash_input(self):
        """text and text_hash are now the same normalised form — keep them in step."""
        note = vault.parse_note("Module 01 - Basics", MODULE)
        for task in note.tasks:
            self.assertEqual(task.text_hash, vault.task_hash(task.text))

    def test_maths_inside_a_task_is_preserved_for_katex(self):
        """26 of 47 real tasks contain maths; the panel is KaTeX-scanned, so the
        delimiters must survive extraction untouched."""
        text = (
            "---\ntype: module\n---\n\n# M\n\n## Study Tasks\n\n"
            r"- [ ] #study Exercise: show $TS^2 \neq S^2 \times \mathbb{R}^2$" "\n"
        )
        note = vault.parse_note("M", text)
        self.assertIn(r"$TS^2 \neq S^2 \times \mathbb{R}^2$", note.tasks[0].text)

    def test_stripping_study_tasks_does_not_lose_the_tasks_themselves(self):
        note = vault.parse_note("Module 01 - Basics", MODULE)
        self.assertEqual(len(note.tasks), 2)

    def test_other_sections_are_untouched(self):
        note = vault.parse_note("A Book", SOURCE)
        self.assertNotIn("## Study Tasks", note.body_markdown)
        self.assertIn("Relevant to", note.body_markdown)
