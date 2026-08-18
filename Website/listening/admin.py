"""Admin for the listening tracker.

This is deliberately the first place the data becomes visible: it gives a working
sortable table before any custom page exists, which is enough to verify that
collection works (scope Phase 3a).
"""

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.utils.timesince import timesince

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

    list_display = ("__str__", "reauth_status", "collection_status", "last_sync_at", "last_sync_status")
    readonly_fields = (
        "reauth_status", "collection_status", "authorized_at", "refresh_token_expires_at",
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
        # The date is formatted BEFORE it reaches format_html: format_html escapes
        # each argument first, so a datetime arrives as a string and a
        # "{:%Y-%m-%d}" spec then raises ValueError. That crash sat on the healthy
        # branch — the one taken for ~160 of the token's 180 days.
        return format_html(
            "OK — expires in {} days ({})",
            days,
            f"{obj.refresh_token_expires_at:%Y-%m-%d}",
        )

    @admin.display(description="Refresh token expires")
    def refresh_token_expires_at(self, obj):
        return obj.refresh_token_expires_at or "—"

    @admin.display(description="Collection")
    def collection_status(self, obj):
        """The second safeguard: is data still arriving, from any cause?

        The re-auth countdown above only catches one way this dies. This catches
        the rest — a stopped timer, a crashed unit, a server that never came back
        up — by reporting when the collector last ran at all.

        Deliberately reports two separate facts rather than one alarm. "No new
        events" is ambiguous on its own: it is equally consistent with a broken
        collector and with simply not having listened to anything. Only the
        collector's own silence is evidence of a fault.
        """
        if not obj.last_sync_at:
            return format_html(
                '<b style="color:#b36b00">Never run</b> — nothing has been collected yet'
            )

        since_run = timezone.now() - obj.last_sync_at
        ran_ago = timesince(obj.last_sync_at)

        latest_event = PlayEvent.objects.order_by("-played_at").values_list(
            "played_at", flat=True
        ).first()
        heard = (
            f"newest observation {timesince(latest_event)} ago"
            if latest_event else "no observations recorded yet"
        )

        hours = since_run.total_seconds() / 3600
        if hours >= 24:
            return format_html(
                '<b style="color:#b00">Collector last ran {} ago</b> — the timer is '
                'not running. ({})', ran_ago, heard,
            )
        if hours >= 6:
            return format_html(
                '<b style="color:#b36b00">Collector last ran {} ago</b> — check the '
                'timer. ({})', ran_ago, heard,
            )
        return format_html("Ran {} ago; {}", ran_ago, heard)
