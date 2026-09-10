"""Tests for blog/markdown_utils.py.

Historically the `blog` app had ZERO tests, which has cost real bugs (the
filter-aware download links, the multi-line `{# #}` comment rendering as page
text). These cover the rendering pipeline — the part most recently changed, and
the part whose failures are silent rather than loud.

The math cases are drawn from the real Fiber Bundles corpus. `protect_math` is
OFF by default, so the first class also pins that blog rendering is unchanged.
"""
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from learning.models import LearningPath, Note

from .markdown_utils import render_markdown_text


class MathProtectionOffByDefaultTests(SimpleTestCase):
    """Blog posts must render exactly as before the learning app existed."""

    def test_default_is_unprotected(self):
        # Markdown eats the backslashes; that is the pre-existing behaviour.
        out = str(render_markdown_text(r"$\{x\}$"))
        self.assertIn("${x}$", out)

    def test_ordinary_markdown_still_renders(self):
        out = str(render_markdown_text("this is *emphasised* and **bold**"))
        self.assertIn("<em>emphasised</em>", out)
        self.assertIn("<strong>bold</strong>", out)

    def test_sanitiser_still_strips_scripts(self):
        out = str(render_markdown_text("hi <script>alert(1)</script>"))
        self.assertNotIn("<script>", out)


class MathProtectionTests(SimpleTestCase):

    def render(self, src):
        return str(render_markdown_text(src, protect_math=True))

    def test_set_builder_braces_survive(self):
        """The real breakage: 10 spans across 9 files in the corpus."""
        out = self.render(r"$S^3 = \{(z_1,z_2) : |z_1|^2 = 1\}$")
        self.assertIn(r"\{(z_1,z_2) : |z_1|^2 = 1\}", out)

    def test_de_rham_worst_case(self):
        src = r"$H^k = \dfrac{\{\text{closed}\}}{\{\text{exact}\}}$"
        self.assertIn(r"\dfrac{\{\text{closed}\}}{\{\text{exact}\}}", self.render(src))

    def test_double_backslash_row_breaks_survive(self):
        """Latent in the corpus today, but breaks the first aligned block written."""
        src = r"$$\begin{aligned} a &= b \\ c &= d \end{aligned}$$"
        out = self.render(src)
        self.assertIn(r"\begin{aligned}", out)
        self.assertIn(r"\\", out)          # the row break, not collapsed to one
        self.assertIn(r"\end{aligned}", out)

    def test_underscore_pair_does_not_become_emphasis(self):
        out = self.render(r"$ _a$ and $b_ $")
        self.assertNotIn("<em>", out)

    def test_subscripts_and_commands_unchanged(self):
        src = r"$t_{\alpha\beta}$ and $F \hookrightarrow E \xrightarrow{\pi} M$"
        out = self.render(src)
        self.assertIn(r"$t_{\alpha\beta}$", out)
        self.assertIn(r"\xrightarrow{\pi}", out)

    def test_display_and_inline_both_survive(self):
        out = self.render("inline $a+b$ then\n\n$$c = d$$\n")
        self.assertIn("$a+b$", out)
        self.assertIn("$$c = d$$", out)

    def test_markdown_outside_math_still_works(self):
        out = self.render(r"*emph* and $x_1$ and **bold**")
        self.assertIn("<em>emph</em>", out)
        self.assertIn("<strong>bold</strong>", out)
        self.assertIn("$x_1$", out)

    def test_restored_math_is_html_escaped(self):
        """Restoration bypasses nh3, so it must not be able to inject markup."""
        out = self.render(r"$a < b$ and $c > d$")
        self.assertIn("&lt;", out)
        self.assertIn("&gt;", out)
        self.assertNotIn("<b>", out)

    def test_script_tag_inside_math_cannot_escape(self):
        out = self.render("$<script>alert(1)</script>$")
        self.assertNotIn("<script>", out)

    def test_token_lookalike_in_prose_is_not_mistaken_for_math(self):
        """A stray placeholder-shaped string must survive untouched."""
        out = self.render("the literal zzmathspanzz9zz appears here")
        self.assertIn("zzmathspanzz9zz", out)

    def test_lone_dollar_is_left_alone(self):
        out = self.render("costs $5 today")
        self.assertIn("$5", out)


class PortfolioLearningCardTests(TestCase):
    """The portfolio's Learning card is database-driven, not hardcoded."""

    def setUp(self):
        self.path = LearningPath.objects.create(
            slug="demo", title="Demo Path", is_public=True, goal="Understand things.",
        )
        Note.objects.create(path=self.path, kind=Note.Kind.MODULE, slug="m1",
                            title="M1", vault_filename="M1")
        Note.objects.create(path=self.path, kind=Note.Kind.CONCEPT, slug="c1",
                            title="C1", vault_filename="C1")

    def test_card_appears_and_links_to_the_path(self):
        response = self.client.get(reverse("portfolio"))
        self.assertContains(response, "Demo Path")
        self.assertContains(response, 'href="/learning/demo/"')

    def test_counts_are_per_kind(self):
        response = self.client.get(reverse("portfolio"))
        self.assertContains(response, "1 module")
        self.assertContains(response, "1 concept")

    def test_archived_notes_are_not_counted(self):
        Note.objects.create(path=self.path, kind=Note.Kind.CONCEPT, slug="gone",
                            title="Gone", vault_filename="Gone", is_archived=True)
        response = self.client.get(reverse("portfolio"))
        self.assertContains(response, "1 concept")

    def test_card_is_hidden_when_no_path_is_public(self):
        """Production has no learning content yet — a hardcoded card would 404."""
        self.path.is_public = False
        self.path.save()
        response = self.client.get(reverse("portfolio"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Demo Path")

    def test_portfolio_still_renders_with_no_learning_app_data_at_all(self):
        LearningPath.objects.all().delete()
        response = self.client.get(reverse("portfolio"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Projects")

    def test_learning_heading_links_to_the_index(self):
        """The card heading is itself a link (annotated request, 2026-09-10)."""
        response = self.client.get(reverse("portfolio"))
        self.assertContains(response, 'href="/learning/"')
