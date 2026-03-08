from django.contrib import admin

# Register your models here.
from django.utils import timezone

from .models import Comment, Poem


@admin.register(Poem)
class PoemAdmin(admin.ModelAdmin):
    list_display = ("title", "date", "slug")
    list_filter = ("date",)
    search_fields = ("title", "excerpt", "content")
    prepopulated_fields = {"slug": ("title",)}


@admin.action(description="Approve selected comments")
def approve_comments(modeladmin, request, queryset):
    queryset.update(is_approved=True, approved_at=timezone.now())


@admin.action(description="Unapprove selected comments")
def unapprove_comments(modeladmin, request, queryset):
    queryset.update(is_approved=False, approved_at=None)


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("post_slug", "user", "created_at", "is_approved")
    list_filter = ("is_approved", "created_at")
    search_fields = ("post_slug", "post_title", "user__username", "content")
    readonly_fields = ("created_at", "updated_at")
    actions = [approve_comments, unapprove_comments]
