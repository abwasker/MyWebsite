"""Phase 3 tests — public reference pages, wikilink resolution, math passthrough.

Notes are built directly here rather than through the importer: these tests are
about RENDERING, and coupling them to the import pipeline would mean an importer
bug fails them too, obscuring which layer broke.
"""
from django.test import TestCase
from django.urls import reverse

from .models import LearningPath, Note, Tag
from .rendering import substitute_wikilinks


class LearningPageTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.path = LearningPath.objects.create(
            slug="demo", title="Demo Path", is_public=True,
            moc_filename="Demo MOC", goal="Understand things.",
        )
        cls.hidden = LearningPath.objects.create(slug="secret", title="Secret", is_public=False)
        cls.tag = Tag.objects.create(path=cls.path, name="demo/alpha",
                                     leaf="alpha", slug="demo-alpha")

        cls.module = Note.objects.create(
            path=cls.path, kind=Note.Kind.MODULE, slug="module-01", title="Module 01 - Basics",
            vault_filename="Module 01 - Basics", part="I - Refreshers", order=1,
            body_markdown="Back to [[Demo MOC]] and see [[Fiber Bundle]].",
        )
        cls.module2 = Note.objects.create(
            path=cls.path, kind=Note.Kind.MODULE, slug="module-02", title="Module 02 - More",
            vault_filename="Module 02 - More", part="I - Refreshers", order=2,
            body_markdown="Second.",
        )
        cls.concept = Note.objects.create(
            path=cls.path, kind=Note.Kind.CONCEPT, slug="fiber-bundle", title="Fiber Bundle",
            vault_filename="Fiber Bundle", module=cls.module,
            body_markdown=(
                r"Set-builder: $S^3 = \{(z_1,z_2) : |z_1|^2 = 1\}$." "\n\n"
                "A link to [[Module 01 - Basics]] and a dead one to [[No Such Note]]."
            ),
        )
        cls.concept.tags.add(cls.tag)
        cls.source = Note.objects.create(
            path=cls.path, kind=Note.Kind.SOURCE, slug="a-book", title="A Book",
            vault_filename="A Book", body_markdown="Cites [[Fiber Bundle]].",
        )
        cls.archived = Note.objects.create(
            path=cls.path, kind=Note.Kind.CONCEPT, slug="gone", title="Gone",
            vault_filename="Gone", is_archived=True, body_markdown="x",
        )

    # ---- routing / visibility -------------------------------------------

    def test_index_lists_public_paths_only(self):
        response = self.client.get(reverse("learning-index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demo Path")
        self.assertNotContains(response, "Secret")

    def test_non_public_path_is_404_not_403(self):
        """Unpublished paths should not even admit to existing."""
        response = self.client.get(reverse("learning-path", args=["secret"]))
        self.assertEqual(response.status_code, 404)

    def test_archived_note_is_404(self):
        response = self.client.get(reverse("learning-concept", args=["demo", "gone"]))
        self.assertEqual(response.status_code, 404)

    def test_wrong_kind_in_url_is_404(self):
        """A concept must not be reachable at the module URL."""
        response = self.client.get(reverse("learning-module", args=["demo", "fiber-bundle"]))
        self.assertEqual(response.status_code, 404)

    def test_all_pages_render_anonymously(self):
        """These are public by design — no login, no permission."""
        for url in (
            reverse("learning-index"),
            reverse("learning-path", args=["demo"]),
            reverse("learning-module", args=["demo", "module-01"]),
            reverse("learning-concept", args=["demo", "fiber-bundle"]),
            reverse("learning-source", args=["demo", "a-book"]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    # ---- generated overview ---------------------------------------------

    def test_overview_groups_modules_by_part_and_orders_them(self):
        response = self.client.get(reverse("learning-path", args=["demo"]))
        self.assertContains(response, "I - Refreshers")
        body = response.content.decode()
        self.assertLess(body.index("Module 01 - Basics"), body.index("Module 02 - More"))

    def test_overview_groups_concepts_by_tag(self):
        response = self.client.get(reverse("learning-path", args=["demo"]))
        self.assertContains(response, "alpha")
        self.assertContains(response, "Fiber Bundle")

    def test_overview_excludes_archived_notes(self):
        response = self.client.get(reverse("learning-path", args=["demo"]))
        self.assertNotContains(response, "Gone")

    # ---- wikilink resolution --------------------------------------------

    def test_wikilink_becomes_a_real_link(self):
        out = substitute_wikilinks("see [[Fiber Bundle]]", self.module)
        self.assertIn("(/learning/demo/concepts/fiber-bundle/)", out)

    def test_piped_wikilink_keeps_its_display_text(self):
        out = substitute_wikilinks("see [[Fiber Bundle|bundles]]", self.module)
        self.assertIn("[bundles](/learning/demo/concepts/fiber-bundle/)", out)

    def test_moc_link_resolves_to_the_path_overview(self):
        """11 real modules link to the MOC, which is generated and never imported."""
        out = substitute_wikilinks("[[Demo MOC]]", self.module)
        self.assertIn("(/learning/demo/)", out)

    def test_broken_link_renders_as_code_not_as_a_link(self):
        response = self.client.get(reverse("learning-concept", args=["demo", "fiber-bundle"]))
        self.assertContains(response, "<code>[[No Such Note]]</code>", html=False)
        self.assertNotContains(response, 'href="/learning/demo/concepts/no-such-note/')

    def test_parenthesised_title_still_produces_a_working_link(self):
        """`U(1)` and `SU(2) and SO(3)` are real note names; bare parens in a
        markdown label would terminate the link syntax early."""
        Note.objects.create(
            path=self.path, kind=Note.Kind.CONCEPT, slug="u1", title="U(1)",
            vault_filename="U(1)", body_markdown="The circle group.",
        )
        linker = Note.objects.create(
            path=self.path, kind=Note.Kind.CONCEPT, slug="linker", title="Linker",
            vault_filename="Linker", body_markdown="See [[U(1)]] here.",
        )
        out = substitute_wikilinks(linker.body_markdown, linker)
        self.assertIn(r"[U\(1\)](/learning/demo/concepts/u1/)", out)

        response = self.client.get(reverse("learning-concept", args=["demo", "linker"]))
        self.assertContains(response, 'href="/learning/demo/concepts/u1/"')
        self.assertContains(response, "U(1)")

    # ---- rendering ------------------------------------------------------

    def test_math_reaches_the_page_intact(self):
        """The 10 real `\\{` spans would otherwise lose their braces."""
        response = self.client.get(reverse("learning-concept", args=["demo", "fiber-bundle"]))
        self.assertContains(response, r"\{(z_1,z_2) : |z_1|^2 = 1\}")

    def test_katex_is_loaded_on_note_pages(self):
        response = self.client.get(reverse("learning-concept", args=["demo", "fiber-bundle"]))
        self.assertContains(response, "katex.min.js")
        self.assertContains(response, "learning-body")

    # ---- navigation panels ----------------------------------------------

    def test_backlinks_panel_lists_linking_notes(self):
        from .models import NoteLink
        NoteLink.objects.create(from_note=self.source, to_note=self.concept,
                                raw_target="Fiber Bundle")
        response = self.client.get(reverse("learning-concept", args=["demo", "fiber-bundle"]))
        self.assertContains(response, "Linked from")
        self.assertContains(response, "A Book")

    def test_backlinks_are_deduplicated(self):
        from .models import NoteLink
        for _ in range(3):
            NoteLink.objects.create(from_note=self.source, to_note=self.concept,
                                    raw_target="Fiber Bundle")
        response = self.client.get(reverse("learning-concept", args=["demo", "fiber-bundle"]))
        self.assertEqual(response.content.decode().count(
            'href="/learning/demo/sources/a-book/"'), 1)

    def test_module_pager_links_to_neighbours(self):
        response = self.client.get(reverse("learning-module", args=["demo", "module-01"]))
        self.assertContains(response, "Module 02 - More")

    def test_concept_pages_have_no_pager(self):
        response = self.client.get(reverse("learning-concept", args=["demo", "fiber-bundle"]))
        self.assertNotContains(response, "learning-pager")

    def test_module_lists_its_concepts(self):
        response = self.client.get(reverse("learning-module", args=["demo", "module-01"]))
        self.assertContains(response, "Concepts in this module")
        self.assertContains(response, "Fiber Bundle")

    # ---- Phase 4 guard ---------------------------------------------------

    def test_no_user_owned_data_in_the_context(self):
        """Nothing private exists yet; keeping it that way is easier than auditing later."""
        response = self.client.get(reverse("learning-concept", args=["demo", "fiber-bundle"]))
        for key in ("user_note", "user_notes", "status", "statuses", "questions", "task_states"):
            self.assertNotIn(key, response.context)


class PathIndexTests(TestCase):
    """The index is a linked destination from /portfolio/, not just an internal list."""

    @classmethod
    def setUpTestData(cls):
        cls.path = LearningPath.objects.create(
            slug="demo", title="Demo Path", is_public=True, goal="Understand things.",
        )
        Note.objects.create(path=cls.path, kind=Note.Kind.MODULE, slug="m1",
                            title="M1", vault_filename="M1")
        Note.objects.create(path=cls.path, kind=Note.Kind.CONCEPT, slug="c1",
                            title="C1", vault_filename="C1")
        Note.objects.create(path=cls.path, kind=Note.Kind.CONCEPT, slug="gone",
                            title="Gone", vault_filename="Gone", is_archived=True)

    def test_index_shows_per_kind_counts_matching_the_portfolio_card(self):
        response = self.client.get(reverse("learning-index"))
        self.assertContains(response, "1 module")
        self.assertContains(response, "1 concept")   # archived one excluded

    def test_index_title_links_to_the_path(self):
        response = self.client.get(reverse("learning-index"))
        self.assertContains(response, 'href="/learning/demo/"')

    def test_index_is_graceful_with_nothing_published(self):
        LearningPath.objects.all().update(is_public=False)
        response = self.client.get(reverse("learning-index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing published yet")
