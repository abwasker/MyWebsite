"""Admin for the listening tracker.

This is deliberately the first place the data becomes visible: it gives a working
sortable table before any custom page exists, which is enough to verify that
collection works (scope Phase 3a).
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import ListeningItem, PlayEvent, SpotifyAuth


def _format_ms(milliseconds):
    """Render milliseconds as h:mm:ss — long podcasts make raw ms unreadable."""
    if not milliseconds:
        return "—"
    total_seconds = int(milliseconds // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


class PlayEventInline(admin.TabularInline):
    """The raw observations behind one item's rollups."""

    model = PlayEvent
    extra = 0
    can_delete = False
    fields = ("played_at", "source", "progress_ms", "resume_position_ms", "fully_played")
    readonly_fields = fields
    ordering = ("-played_at",)

    def has_add_permission(self, request, obj):
        return False  # events come from the collector, never by hand


@admin.register(ListeningItem)
class ListeningItemAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "creator_name",
        "item_type",
        "first_played_at",
        "last_played_at",
        "observation_count",
        "estimated_time",
        "fully_played",
    )
    list_filter = ("item_type", "fully_played", "last_played_at")
    search_fields = ("name", "creator_name", "show_name")
    date_hierarchy = "last_played_at"
    ordering = ("-last_played_at",)
    inlines = (PlayEventInline,)

    readonly_fields = (
        "spotify_id", "item_type", "name", "creator_name", "show_name",
        "show_spotify_id", "duration_ms", "spotify_url", "image_url",
        "first_played_at", "last_played_at", "observation_count",
        "max_progress_ms", "fully_played", "created_at", "updated_at",
        "listen_link", "cover",
    )
    fieldsets = (
        (None, {"fields": ("name", "creator_name", "item_type", "cover", "listen_link")}),
        ("Podcast", {"fields": ("show_name", "show_spotify_id"), "classes": ("collapse",)}),
        ("Listening", {"fields": (
            "first_played_at", "last_played_at", "observation_count",
            "max_progress_ms", "fully_played", "duration_ms",
        )}),
        ("Identity", {"fields": ("spotify_id", "spotify_url", "image_url", "created_at", "updated_at"),
                      "classes": ("collapse",)}),
    )

    def has_add_permission(self, request):
        return False  # items are discovered by the collector

    @admin.display(description="Est. time")
    def estimated_time(self, obj):
        """Listening time is INFERRED, not measured — the API publishes no
        ms_played. The tilde is deliberate: it should never read as exact."""
        value = obj.estimated_listened_ms
        return f"~{_format_ms(value)}" if value else "—"

    @admin.display(description="Open in Spotify")
    def listen_link(self, obj):
        if not obj.spotify_url:
            return "—"
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', obj.spotify_url, obj.spotify_url)

    @admin.display(description="Cover")
    def cover(self, obj):
        if not obj.image_url:
            return "—"
        return format_html('<img src="{}" style="max-height:120px;border-radius:6px" alt="" />', obj.image_url)


@admin.register(PlayEvent)
class PlayEventAdmin(admin.ModelAdmin):
    """Read-only view of the raw log, for auditing what the collector saw."""

    list_display = ("played_at", "item", "source", "progress_ms", "fully_played")
    list_filter = ("source", "fully_played", "played_at")
    search_fields = ("item__name", "item__creator_name")
    date_hierarchy = "played_at"
    ordering = ("-played_at",)
    list_select_related = ("item",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # append-only: the log is evidence, not editable data


@admin.register(SpotifyAuth)
class SpotifyAuthAdmin(admin.ModelAdmin):
    """Token state, existing mainly to make the silent 6-month expiry visible."""

    list_display = ("__str__", "reauth_status", "last_sync_at", "last_sync_status")
    readonly_fields = (
        "reauth_status", "authorized_at", "refresh_token_expires_at",
        "refresh_token_fingerprint", "scopes", "access_token_expires_at",
        "last_sync_at", "last_sync_status", "updated_at",
    )
    exclude = ("access_token",)  # no reason to render a bearer token in a web page

    def has_add_permission(self, request):
        return False  # singleton, created on demand

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Re-authorization")
    def reauth_status(self, obj):
        """The whole point of this model.

        A lapsed refresh token stops collection with no error and no symptom, so
        the countdown is surfaced loudly rather than left to be discovered.
        """
        days = obj.days_until_reauth
        if days is None:
            return format_html('<b style="color:#b00">Never authorized</b> — run manage.py spotify_authorize')
        if days < 0:
            return format_html('<b style="color:#b00">EXPIRED {} days ago</b> — collection has stopped; re-run manage.py spotify_authorize', abs(days))
        if days < 21:
            return format_html('<b style="color:#b36b00">Expires in {} days</b> — re-authorize soon', days)
        return format_html('OK — expires in {} days ({:%Y-%m-%d})', days, obj.refresh_token_expires_at)

    @admin.display(description="Refresh token expires")
    def refresh_token_expires_at(self, obj):
        return obj.refresh_token_expires_at or "—"
