from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
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
