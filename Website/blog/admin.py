from django.contrib import admin, messages

from django.utils import timezone
from django.utils.html import format_html

from .markdown_utils import missing_embed_references, render_markdown_text
from .models import BlogPost, Category, Comment, ContentImage, Poem, Tag


class EmbedWarningMixin:
    """Warn — without blocking — about `![[name]]` embeds matching no image.

    A lookup miss renders as literal text rather than raising (see
    `normalize_obsidian_embeds`), because a typo must never 500 a published
    page. The cost of that choice is silence: nothing tells the author, and the
    only detection is reading the published page. This closes the gap at save
    time, where it is cheap to fix, and stays non-blocking so a deliberate
    forward reference is still saveable (§4.12.1 Gap 3).

    ⚠️ Runs in `save_related`, NOT `clean` or `save_model`. ContentImages arrive
    as INLINES in the same POST, and at clean/save_model time they are not yet
    committed — so the lookup would come back empty and every embed would be
    reported as a miss on a first save. `save_related` runs after the inline
    formsets commit, so it sees images added in the very same submission.
    """

    #: Name of the field holding the markdown to scan. Set on each admin,
    #: since posts and poems keep their body under different names.
    embed_source_field = None

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)

        missing = missing_embed_references(
            getattr(form.instance, self.embed_source_field, "") or "",
            form.instance,
        )
        if not missing:
            return

        messages.warning(
            request,
            "{} image {} no uploaded image and will publish as literal text: {}. "
            "Check the reference name, or copy the ready-made snippet from the "
            "Images section below.".format(
                len(missing),
                "reference matches" if len(missing) == 1 else "references match",
                ", ".join("![[{}]]".format(name) for name in missing),
            ),
        )


class ContentImageInlineBase(admin.TabularInline):
    model = ContentImage
    extra = 1
    fields = (
        "image",
        "reference_name",
        "alt_text",
        "image_preview",
        "obsidian_embed",
        "markdown_embed",
        "created_at",
    )
    readonly_fields = (
        "image_preview",
        "obsidian_embed",
        "markdown_embed",
        "created_at",
    )

    @admin.display(description="Preview")
    def image_preview(self, obj):
        if obj.pk and obj.image:
            return format_html(
                '<img src="{}" alt="{}" style="max-width: 140px; max-height: 90px; object-fit: cover;" />',
                obj.image.url,
                obj.alt_text or obj.reference_name,
            )
        return "Save to preview."

    @admin.display(description="Obsidian embed")
    def obsidian_embed(self, obj):
        if obj.reference_name:
            return format_html("<code>![[{}]]</code>", obj.reference_name)
        return "Add a reference name."

    @admin.display(description="Markdown embed")
    def markdown_embed(self, obj):
        if obj.pk and obj.image:
            alt_text = obj.alt_text or obj.reference_name
            return format_html("<code>![{}]({})</code>", alt_text, obj.image.url)
        return "Save to generate URL."


class BlogPostContentImageInline(ContentImageInlineBase):
    fk_name = "post"


class PoemContentImageInline(ContentImageInlineBase):
    fk_name = "poem"


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(BlogPost)
class BlogPostAdmin(EmbedWarningMixin, admin.ModelAdmin):
    embed_source_field = "markdown_body"
    list_display = ("title", "status", "published_at", "category", "allow_comments")
    list_filter = ("status", "category", "tags", "allow_comments", "published_at")
    search_fields = ("title", "excerpt", "markdown_body")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("tags",)
    readonly_fields = ("rendered_preview", "created_at", "updated_at")
    inlines = (BlogPostContentImageInline,)
    fieldsets = (
        (None, {"fields": ("title", "slug", "excerpt", "markdown_body")}),
        (
            "Publishing",
            {
                "fields": (
                    "status",
                    "published_at",
                    "author_name",
                    "cover_image",
                    "cover_upload",
                    "category",
                    "tags",
                    "allow_comments",
                )
            },
        ),
        ("Preview", {"fields": ("rendered_preview",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Rendered preview")
    def rendered_preview(self, obj):
        if not obj.pk:
            return "Save this post to generate a preview."
        return format_html(
            '<div style="max-width: 760px; padding: 1rem; border: 1px solid #ddd; border-radius: 8px;">{}</div>',
            render_markdown_text(obj.markdown_body, obj),
        )


@admin.register(Poem)
class PoemAdmin(EmbedWarningMixin, admin.ModelAdmin):
    embed_source_field = "content"
    list_display = ("title", "date", "slug")
    list_filter = ("date",)
    search_fields = ("title", "excerpt", "content")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("rendered_preview",)
    autocomplete_fields = ("related_musing_post",)
    inlines = (PoemContentImageInline,)
    fieldsets = (
        (None, {"fields": ("title", "slug", "date", "excerpt", "content")}),
        (
            "Related musing",
            {
                "fields": ("related_musing_post",),
                "description": "Optional. Link this poem to a published blog post where the musing and comments live.",
            },
        ),
        ("Preview", {"fields": ("rendered_preview",)}),
    )

    @admin.display(description="Rendered preview")
    def rendered_preview(self, obj):
        if not obj.pk:
            return "Save this poem to generate a preview."
        return format_html(
            '<div style="max-width: 760px; padding: 1rem; border: 1px solid #ddd; border-radius: 8px;">{}</div>',
            render_markdown_text(obj.content, obj),
        )


@admin.register(ContentImage)
class ContentImageAdmin(admin.ModelAdmin):
    list_display = ("reference_name", "owner", "created_at", "image_preview")
    list_filter = ("created_at",)
    search_fields = ("reference_name", "alt_text", "post__title", "poem__title")
    readonly_fields = ("created_at", "image_preview", "obsidian_embed", "markdown_embed")

    @admin.display(description="Owner")
    def owner(self, obj):
        return obj.post or obj.poem

    @admin.display(description="Preview")
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" alt="{}" style="max-width: 180px; max-height: 120px; object-fit: cover;" />',
                obj.image.url,
                obj.alt_text or obj.reference_name,
            )
        return "No image."

    @admin.display(description="Obsidian embed")
    def obsidian_embed(self, obj):
        return format_html("<code>![[{}]]</code>", obj.reference_name)

    @admin.display(description="Markdown embed")
    def markdown_embed(self, obj):
        if obj.image:
            alt_text = obj.alt_text or obj.reference_name
            return format_html("<code>![{}]({})</code>", alt_text, obj.image.url)
        return "Save to generate URL."


@admin.action(description="Approve selected comments")
def approve_comments(modeladmin, request, queryset):
    queryset.update(is_approved=True, approved_at=timezone.now())


@admin.action(description="Unapprove selected comments")
def unapprove_comments(modeladmin, request, queryset):
    queryset.update(is_approved=False, approved_at=None)


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("post_title", "user", "created_at", "is_approved", "post")
    list_filter = ("is_approved", "created_at", "post")
    search_fields = ("post_slug", "post_title", "post__title", "user__username", "content")
    readonly_fields = ("created_at", "updated_at")
    actions = [approve_comments, unapprove_comments]
