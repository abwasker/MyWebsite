"""The collector. Run it on a timer; it is safe to run twice.

    python manage.py spotify_sync

Everything interesting lives in ``listening/sync.py`` — this is the shell that
turns a run into terminal output and an exit code.

EXIT CODES ARE CHOSEN FOR A SYSTEMD TIMER
-----------------------------------------
This runs unattended every few minutes, so what counts as "failure" matters.
Nothing playing, a rate limit, a dropped connection — these are the *normal*
weather of a poller, and reporting them as unit failures would light up
``systemctl`` so often that a real failure would be invisible among them. They
exit **0**.

A non-zero exit is reserved for the two things a human actually has to fix:
missing credentials, and a refresh token that has lapsed or been revoked. That
one matters — the token expires at about six months and, left alone, stops
collection with no error and no symptom.
"""

from django.core.management.base import BaseCommand, CommandError

from listening.sync import run_sync


class Command(BaseCommand):
    help = "Poll Spotify and record what was listened to. Idempotent; safe on a timer."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and report what would be recorded, without writing any "
                 "listening data. (May still refresh the cached access token.)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="How many recently-played songs to request (max 50, the default).",
        )

    def handle(self, *args, **options):
        summary = run_sync(limit=options["limit"], dry_run=options["dry_run"])

        if summary["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN — nothing written"))

        # What the poll saw. Say so explicitly: for podcasts this call is the
        # only evidence that exists, so "did it see anything?" is the question.
        if summary["now_playing"]:
            self.stdout.write(f"  now playing : {summary['now_playing']}")
        elif summary["skipped"]:
            self.stdout.write(f"  now playing : nothing recorded — {summary['skipped']}")

        if summary["outcome"] == "fatal":
            # CommandError exits non-zero, which is what a human needs to notice.
            raise CommandError(
                f"{summary['status']}: {summary['detail']}\n"
                "If this mentions invalid_grant, the refresh token has lapsed — "
                "re-run 'manage.py spotify_authorize' locally, then copy the new "
                "value into /etc/anotiontoponder.env and restart the timer."
            )

        if summary["outcome"] == "transient":
            # Deliberately exits 0: the next run picks this up by itself.
            self.stdout.write(self.style.WARNING(
                f"  {summary['status']} — {summary['detail']}"
            ))
            if summary["retry_after"]:
                self.stdout.write(f"  retry after : {summary['retry_after']}s")
            self.stdout.write("  (transient — the next run will retry)")
            return

        if summary["dry_run"]:
            self.stdout.write(f"  {summary['detail']}")
            return

        self.stdout.write(
            f"  recorded    : {summary['episode_observations']} episode observation(s), "
            f"{summary['track_plays']} song play(s)"
        )
        if summary["items_created"]:
            self.stdout.write(f"  new items   : {summary['items_created']}")

        wrote_anything = summary["episode_observations"] or summary["track_plays"]
        self.stdout.write(
            self.style.SUCCESS("  done.") if wrote_anything
            else "  done. (nothing new — expected on most polls)"
        )
