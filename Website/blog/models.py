from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.db import models
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class BlogPostQuerySet(models.QuerySet):
    def published(self):
        return self.filter(
            status=self.model.Status.PUBLISHED,
            published_at__lte=timezone.now(),
        )


class BlogPost(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    title = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, unique=True)
    excerpt = models.CharField(max_length=320)
    markdown_body = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    author_name = models.CharField(max_length=120, default="Anosh")
    cover_image = models.CharField(max_length=255, blank=True)
    cover_upload = models.ImageField(upload_to="blog/covers/%Y/%m/", blank=True)
    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="posts",
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="posts")
    allow_comments = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = BlogPostQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-id"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("post-details", args=[self.slug])

    @property
    def cover_upload_url(self):
        """URL of the uploaded cover — but only if the file is actually there.

        ⚠️ `{% if post.cover_upload %}` is true whenever the DATABASE FIELD holds
        a name. It says nothing about whether the file exists. Per §4.12 media is
        gitignored AND absent from `mysqldump`, so a row routinely outlives its
        file — pulling production content to a dev machine does exactly that.
        Templates that branch on the field alone then emit an `<img>` at a
        missing path, and the reader gets a broken image with **nothing in any
        log**. `the-divine-council` is in that state locally right now.

        Returning "" here lets the template's existing `{% elif %}` / `{% else %}`
        chain fall through to the default cover, which is the visible, correct
        outcome rather than a silent one.

        Cost: one `storage.exists()` per post per render — a single stat call on
        a local filesystem. Cheap against the failure it prevents, but worth
        revisiting if a listing ever pages to hundreds of posts.

        See `cover_static_url` for the same guard on the other cover field.
        """
        name = self.cover_upload.name
        if not name:
            return ""
        try:
            if self.cover_upload.storage.exists(name):
                return self.cover_upload.url
        except (OSError, ValueError):
            # Unreadable storage or a malformed name: treat as missing rather
            # than 500 the page. A broken cover must never take a post down.
            pass
        return ""

    @property
    def cover_static_url(self):
        """URL of the static cover — but only if that file actually exists.

        ⚠️ An earlier version of this guard covered only `cover_upload`, on the
        reasoning that `static/` files are committed to git so a missing one is
        a broken deploy rather than data drift. **That was wrong, and
        `the-divine-council` proved it:** `cover_image` is a free-text CharField
        typed into the admin. A typo or a stale filename is DATA, exactly like a
        media row whose file is gone — it just fails in a different directory.

        Failure mode without this: `{% static %}` happily builds a URL for a file
        that is not there (the project uses Django's plain StaticFilesStorage, so
        nothing validates it), the page returns 200, and the reader gets a broken
        image. Nothing appears in any log.

        `finders.find()` is used rather than `staticfiles_storage.exists()`
        because it searches the SOURCE directories, which are present both in
        development and on the server; `staticfiles_storage` only sees what
        `collectstatic` has gathered, so it would disagree between the two.

        Cost: a few `os.path.exists` calls per post per render, uncached on
        purpose — caching a negative result would mean adding the missing file
        did not fix the page until the process restarted, which is a worse
        surprise than the lookup is expensive.
        """
        name = (self.cover_image or "").strip()
        if not name:
            return ""
        path = f"blog/images/{name}"
        try:
            if finders.find(path):
                return static(path)
        except (OSError, ValueError, SuspiciousFileOperation):
            pass
        return ""


class Poem(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, max_length=220)
    date = models.DateField()
    excerpt = models.CharField(max_length=280)
    content = models.TextField()
    related_musing_post = models.ForeignKey(
        BlogPost,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="linked_poems",
    )

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("poem-detail", args=[self.slug])


class ContentImage(models.Model):
    post = models.ForeignKey(
        BlogPost,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="content_images",
    )
    poem = models.ForeignKey(
        Poem,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="content_images",
    )
    image = models.ImageField(upload_to="blog/content/%Y/%m/")
    reference_name = models.CharField(max_length=255)
    alt_text = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["reference_name", "-created_at"]

    def clean(self):
        super().clean()
        has_post = self.post_id is not None or self.post is not None
        has_poem = self.poem_id is not None or self.poem is not None
        if has_post == has_poem:
            raise ValidationError("Attach each content image to exactly one blog post or poem.")

    def __str__(self):
        return self.reference_name


class Comment(models.Model):
    post = models.ForeignKey(
        BlogPost,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    post_slug = models.SlugField(max_length=255, db_index=True)
    post_title = models.CharField(max_length=255)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    content = models.TextField(max_length=1200)
    is_approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["post_slug", "is_approved"]),
            models.Index(fields=["post", "is_approved"]),
        ]

    def __str__(self):
        return f"{self.user} on {self.post_slug}"
