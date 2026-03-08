from django.db import models

# Create your models here.
from django.db import models
from django.urls import reverse
from django.conf import settings


class Poem(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, max_length=220)
    date = models.DateField()
    excerpt = models.CharField(max_length=280)
    content = models.TextField()

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("poem-detail", args=[self.slug])


class Comment(models.Model):
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
        ]

    def __str__(self):
        return f"{self.user} on {self.post_slug}"
