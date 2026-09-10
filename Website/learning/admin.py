"""Admin for the `learning` app.

TWO DELIBERATE ABSENCES — do not "fix" these without reading the scope:

1. The USER-OWNED models (UserNote, NoteStatus, TaskState, Question) are NOT
   registered. Django's admin is the fourth door onto private data (scope §4.2):
   a staff user holding `learning.view_usernote` would read every user's private
   notes here, without ever touching the gated pages. Leaving them unregistered
   means the only way to read them is a shell — which is the intended friction.

2. `LearningAccess` is NOT registered. It is a permission anchor with no table
   and no rows; there is nothing to list.

The VAULT-SOURCED models are registered READ-ONLY. They are owned by the
importer and upserted on every run, so an edit made here is silently discarded
the next time the importer runs — a footgun that looks like it worked.
"""
from django.contrib import admin

from .models import LearningPath, Note, NoteLink, StudyTask, Tag


class ReadOnlyAdmin(admin.ModelAdmin):
    """Browsable, never editable. See the module docstring for why."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(LearningPath)
class LearningPathAdmin(admin.ModelAdmin):
    """The ONE partly-editable vault-sourced model.

    `is_public` and `goal` are EDITORIAL, not importer-owned — publishing a path
    is a decision, never a side effect of an import, which is why `is_public`
    defaults to False and no import ever changes it. The remaining fields are
    importer-owned and stay read-only.
    """

    list_display = ("title", "slug", "is_public", "imported_at")
    list_filter = ("is_public",)
    search_fields = ("title", "slug")
    fields = ("title", "goal", "is_public", "slug", "vault_folder",
              "moc_filename", "imported_at")
    readonly_fields = ("slug", "vault_folder", "moc_filename", "imported_at")

    def has_add_permission(self, request):
        return False   # paths are created by the importer

    def has_delete_permission(self, request, obj=None):
        return False   # deleting cascades to every note and every user row


@admin.register(Note)
class NoteAdmin(ReadOnlyAdmin):
    list_display = ("title", "kind", "path", "part", "module", "is_archived", "imported_at")
    list_filter = ("path", "kind", "is_archived", "part")
    search_fields = ("title", "vault_filename", "body_markdown")


@admin.register(Tag)
class TagAdmin(ReadOnlyAdmin):
    list_display = ("name", "leaf", "path")
    list_filter = ("path",)
    search_fields = ("name",)


@admin.register(NoteLink)
class NoteLinkAdmin(ReadOnlyAdmin):
    list_display = ("from_note", "raw_target", "to_note", "display_text")
    list_filter = ("from_note__path",)
    search_fields = ("raw_target", "display_text")


@admin.register(StudyTask)
class StudyTaskAdmin(ReadOnlyAdmin):
    list_display = ("note", "kind", "order", "text")
    list_filter = ("kind", "note__path")
    search_fields = ("text",)
