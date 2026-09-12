"""Tests for blog/markdown_utils.py.

Historically the `blog` app had ZERO tests, which has cost real bugs (the
filter-aware download links, the multi-line `{# #}` comment rendering as page
text). These cover the rendering pipeline — the part most recently changed, and
the part whose failures are silent rather than loud.

The math cases are drawn from the real Fiber Bundles corpus. `protect_math` is
OFF by default, so the first class also pins that blog rendering is unchanged.
"""
import re

from django.conf import settings
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


# ---------------------------------------------------------------------------
# Obsidian image embeds — §4.12.1
#
# These are the FIRST tests of normalize_obsidian_embeds, which has shipped
# untested since it was written. They are split deliberately:
#
#   *CurrentBehaviourTests pin what already works, so Phase 2 cannot break it.
#   *SizingTests describe behaviour that DOES NOT EXIST YET and must fail now.
#   *AttributeAllowlistTests are the security guard, and are mutation-tested.
# ---------------------------------------------------------------------------
from .models import BlogPost, ContentImage


class ObsidianEmbedBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.post = BlogPost.objects.create(
            title="Embed Fixture",
            slug="embed-fixture",
            excerpt="fixture",
            markdown_body="",
        )
        # A string may be assigned straight to an ImageField: .url resolves
        # through MEDIA_URL without the file needing to exist on disk. These
        # tests are about the rewriter, not about storage.
        cls.image = ContentImage.objects.create(
            post=cls.post,
            image="blog/content/2026/09/diagram.png",
            reference_name="diagram.png",
            alt_text="a field diagram",
        )

    def render(self, src):
        return str(render_markdown_text(src, self.post))


class ObsidianEmbedCurrentBehaviourTests(ObsidianEmbedBase):
    """What already works. Phase 2 must not regress any of it."""

    def test_bare_embed_resolves_to_an_img(self):
        out = self.render("![[diagram.png]]")
        self.assertIn("<img", out)
        self.assertIn("diagram.png", out)
        self.assertNotIn("[[", out)

    def test_alt_falls_back_to_the_row(self):
        self.assertIn('alt="a field diagram"', self.render("![[diagram.png]]"))

    def test_inline_pipe_text_overrides_the_row_alt(self):
        self.assertIn('alt="a caption"', self.render("![[diagram.png|a caption]]"))

    def test_unknown_reference_is_returned_verbatim(self):
        # Gap 3: a miss must never raise, because that would 500 a published
        # page over a typo. It stays literal until the admin warning lands.
        out = self.render("![[no-such-image.png]]")
        self.assertIn("[[no-such-image.png]]", out)
        self.assertNotIn("<img", out)

    def test_markdown_embed_form_is_left_alone(self):
        # ever-virgin-mary on production uses this form; it bypasses the
        # rewriter entirely and must keep working untouched.
        out = self.render("![a caption](/media/blog/content/2026/09/x.jpeg)")
        self.assertIn('src="/media/blog/content/2026/09/x.jpeg"', out)


class ObsidianEmbedSizingTests(ObsidianEmbedBase):
    """Phase 2 target behaviour. EVERY TEST HERE MUST FAIL BEFORE PHASE 2.

    Obsidian's rule, verified in the vault 2026-09-11: a numeric pipe value is
    a SIZE, any other text is ALT TEXT. Disambiguated by shape.
    """

    def test_numeric_pipe_becomes_a_width(self):
        out = self.render("![[diagram.png|300]]")
        self.assertIn('width="300"', out)
        self.assertNotIn('alt="300"', out)

    def test_numeric_pipe_keeps_the_row_alt_text(self):
        self.assertIn('alt="a field diagram"', self.render("![[diagram.png|300]]"))

    def test_dimension_pair_sets_both(self):
        out = self.render("![[diagram.png|300x200]]")
        self.assertIn('width="300"', out)
        self.assertIn('height="200"', out)

    def test_non_integer_values_are_alt_text_not_sizes(self):
        # Obsidian sizes on positive integers only. Anything else is a caption,
        # and must not leak into a width attribute.
        for value in ["0", "-5", "3.5", "300px", "1e3", "٣٠٠"]:
            with self.subTest(value=value):
                out = self.render(f"![[diagram.png|{value}]]")
                self.assertNotIn("width=", out)
                self.assertIn(f'alt="{value}"', out)

    def test_size_survives_inline_in_a_paragraph(self):
        out = self.render("before ![[diagram.png|300]] after")
        self.assertIn('width="300"', out)
        self.assertIn("before", out)
        self.assertIn("after", out)

    def test_alt_text_is_escaped_when_we_emit_raw_html(self):
        # THE hazard Phase 2 introduces: emitting <img> ourselves takes the
        # escaping away from Markdown. A quote in alt text must not be able to
        # close the attribute and add another.
        self.image.alt_text = 'evil" onerror="alert(1)'
        self.image.save()
        out = self.render("![[diagram.png|300]]")
        # The word "onerror" DOES survive — harmlessly, as text inside the alt
        # value. What must not survive is the quote that would close the
        # attribute and start a real handler. Assert that, not the substring.
        self.assertIn("&quot;", out)
        self.assertNotIn('onerror="', out)
        self.assertNotIn('alt="evil"', out)


class ImgAttributeAllowlistTests(SimpleTestCase):
    """The sanitiser guard. Widening it is the point of Phase 3, so these pin
    exactly how far it is allowed to open."""

    def test_style_is_stripped(self):
        # Not primarily a script vector — a full-viewport overlay needs no JS.
        out = str(render_markdown_text(
            '<img src="/m/x.png" alt="a" '
            'style="position:fixed;top:0;left:0;width:100vw;height:100vh">'
        ))
        self.assertNotIn("style=", out)
        self.assertNotIn("position:fixed", out)

    def test_class_is_stripped(self):
        # Reserved for alignment (§4.12.1, parked). Until then, nothing.
        out = str(render_markdown_text('<img src="/m/x.png" alt="a" class="anything">'))
        self.assertNotIn("class=", out)

    def test_event_handlers_are_stripped(self):
        out = str(render_markdown_text('<img src="/m/x.png" alt="a" onerror="alert(1)">'))
        self.assertNotIn("onerror", out)

    def test_javascript_scheme_src_is_stripped(self):
        out = str(render_markdown_text('<img src="javascript:alert(1)" alt="a">'))
        self.assertNotIn("javascript:", out)


class EmbedLayoutContractTests(ObsidianEmbedBase):
    """Layout behaviours that are INTENDED, not accidents (§4.12.1).

    Both come from `<img>` being an inline element inside Markdown's paragraph
    rules. They were reviewed on a rendered page 2026-09-11 and kept. A future
    `display: block` on the img rule would silently remove both, which is why
    they are pinned here and why the stylesheets carry a warning comment.
    """

    def test_consecutive_embeds_share_one_paragraph(self):
        # Markdown puts adjacent lines in ONE <p>, so the images sit side by
        # side. This is the documented way to place two images on a row.
        out = self.render("![[diagram.png|300]]\n![[diagram.png|300]]")
        self.assertEqual(out.count("<p>"), 1, "consecutive embeds must share one paragraph")
        self.assertEqual(out.count("<img"), 2)

    def test_blank_line_between_embeds_stacks_them(self):
        # ...and a blank line is how an author opts out and stacks them.
        out = self.render("![[diagram.png|300]]\n\n![[diagram.png|300]]")
        self.assertEqual(out.count("<p>"), 2, "a blank line must split them into two paragraphs")

    def test_image_stays_inside_the_sentence(self):
        # Mid-sentence embedding is supported; the img must not be lifted out
        # of the paragraph into a sibling of its own.
        out = self.render("The icon ![[diagram.png|120]] sits mid-sentence.")
        self.assertEqual(out.count("<p>"), 1)
        self.assertRegex(out, r"<p>The icon <img[^>]*> sits mid-sentence\.</p>")


class StylesheetParityTests(SimpleTestCase):
    """`.post-body img` and `.poem-content img` are declared in two files, and
    poem pages load ONLY poetry.css. Updating one and not the other is the most
    likely way an image change ships half-broken, so compare the declarations."""

    DECLARATIONS = re.compile(r"\{([^}]*)\}")

    def _img_declarations(self, path):
        source = (settings.BASE_DIR / path).read_text(encoding="utf-8")
        index = source.index(".poem-content img")
        body = self.DECLARATIONS.search(source[index:]).group(1)
        return sorted(
            line.strip().rstrip(";")
            for line in body.split(";")
            if line.strip()
        )

    def test_img_rules_match_across_stylesheets(self):
        post = self._img_declarations("blog/static/blog/post-detail.css")
        poem = self._img_declarations("blog/static/blog/poetry.css")
        self.assertEqual(post, poem, "the two body-image rules have drifted apart")
        # Guard against the comparison passing vacuously on two empty rules.
        self.assertIn("vertical-align: middle", post)
        self.assertGreaterEqual(len(post), 5)


# --- §4.12.1 Gap 3: the admin warns about embeds that match no image ---------

import base64
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from .markdown_utils import missing_embed_references
from .models import Poem

# Smallest valid PNG. ImageField validation runs it through Pillow, so the
# bytes must really decode - a text file named .png is rejected.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class MissingEmbedReferenceTests(ObsidianEmbedBase):
    """The helper itself, apart from any admin plumbing."""

    def test_resolved_reference_is_not_reported(self):
        self.assertEqual(missing_embed_references("![[diagram.png]]", self.post), [])

    def test_unknown_reference_is_reported(self):
        self.assertEqual(
            missing_embed_references("![[nope.png]]", self.post), ["nope.png"]
        )

    def test_repeats_are_collapsed_and_order_is_kept(self):
        out = missing_embed_references("![[b.png]] ![[a.png]] ![[b.png]]", self.post)
        self.assertEqual(out, ["b.png", "a.png"])

    def test_a_row_with_no_file_is_still_a_miss(self):
        # The rewriter's lookup skips rows whose image is empty, so such an
        # embed publishes literally. The warning must agree with that.
        ContentImage.objects.create(
            post=self.post, image="", reference_name="empty.png"
        )
        self.assertEqual(
            missing_embed_references("![[empty.png]]", self.post), ["empty.png"]
        )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class EmbedWarningAdminTests(TestCase):
    """The warning as an author actually meets it: a real admin POST."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("embedadmin", "e@example.com", "pw")
        cls.post = BlogPost.objects.create(
            title="Warn Fixture", slug="warn-fixture",
            excerpt="fixture", markdown_body="",
        )
        cls.image = ContentImage.objects.create(
            post=cls.post,
            image="blog/content/2026/09/diagram.png",
            reference_name="diagram.png",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _post_body(self, markdown_body, **overrides):
        """Change-form POST for the fixture post, inlines left as-is."""
        data = {
            "title": self.post.title,
            "slug": self.post.slug,
            "excerpt": self.post.excerpt,
            "markdown_body": markdown_body,
            "status": self.post.status,
            "author_name": self.post.author_name,
            "cover_image": "",
            "allow_comments": "on",
            "content_images-TOTAL_FORMS": "1",
            "content_images-INITIAL_FORMS": "1",
            "content_images-MIN_NUM_FORMS": "0",
            "content_images-MAX_NUM_FORMS": "1000",
            "content_images-0-id": str(self.image.pk),
            "content_images-0-post": str(self.post.pk),
            "content_images-0-reference_name": self.image.reference_name,
            "content_images-0-alt_text": "",
        }
        data.update(overrides)
        return self.client.post(
            reverse("admin:blog_blogpost_change", args=[self.post.pk]),
            data, follow=True,
        )

    @staticmethod
    def _warnings(response):
        return [
            m.message for m in response.context["messages"]
            if m.level_tag == "warning"
        ]

    def test_typo_warns_and_names_the_reference(self):
        warnings = self._warnings(self._post_body("![[diagrma.png]]"))
        self.assertEqual(len(warnings), 1)
        self.assertIn("![[diagrma.png]]", warnings[0])

    def test_resolved_reference_does_not_warn(self):
        self.assertEqual(self._warnings(self._post_body("![[diagram.png]]")), [])

    def test_the_save_still_succeeds(self):
        # Non-blocking by design: a warning must not cost the author their edit.
        self._post_body("![[diagrma.png]] and prose")
        self.post.refresh_from_db()
        self.assertEqual(self.post.markdown_body, "![[diagrma.png]] and prose")

    def test_image_added_in_the_SAME_save_does_not_warn(self):
        # THE regression guard for running in save_related rather than clean:
        # this image does not exist when the form is cleaned, only after the
        # inline formset commits. In clean() every embed here would be a miss.
        response = self._post_body(
            "![[fresh.png]]",
            **{
                "content_images-TOTAL_FORMS": "2",
                "content_images-1-id": "",
                "content_images-1-post": str(self.post.pk),
                "content_images-1-reference_name": "fresh.png",
                "content_images-1-alt_text": "",
                "content_images-1-image": SimpleUploadedFile(
                    "fresh.png", _PNG, content_type="image/png"
                ),
            },
        )
        self.assertTrue(
            self.post.content_images.filter(reference_name="fresh.png").exists(),
            "precondition: the inline image must actually have been created",
        )
        self.assertEqual(self._warnings(response), [])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PoemEmbedWarningAdminTests(TestCase):
    """Poems carry the same inline, so they need the same warning."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("poemadmin", "p@example.com", "pw")
        cls.poem = Poem.objects.create(
            title="Warn Poem", slug="warn-poem",
            date="2026-09-12", excerpt="fixture", content="",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_typo_in_a_poem_warns(self):
        response = self.client.post(
            reverse("admin:blog_poem_change", args=[self.poem.pk]),
            {
                "title": self.poem.title,
                "slug": self.poem.slug,
                "date": "2026-09-12",
                "excerpt": self.poem.excerpt,
                "content": "![[missing.png]]",
                "content_images-TOTAL_FORMS": "0",
                "content_images-INITIAL_FORMS": "0",
                "content_images-MIN_NUM_FORMS": "0",
                "content_images-MAX_NUM_FORMS": "1000",
            },
            follow=True,
        )
        warnings = [
            m.message for m in response.context["messages"]
            if m.level_tag == "warning"
        ]
        self.assertEqual(len(warnings), 1)
        self.assertIn("![[missing.png]]", warnings[0])
