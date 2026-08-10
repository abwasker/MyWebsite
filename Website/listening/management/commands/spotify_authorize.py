"""One-time (well, twice-yearly) Spotify authorization.

WHY THIS IS A MANAGEMENT COMMAND AND NOT A SCRIPT
-------------------------------------------------
Spotify refresh tokens last about six months, and refreshing an access token does
not extend that. When one lapses, collection stops **silently** — no error, just
no new rows. Re-authorizing therefore has to be possible months from now, which
means it must live in the repo rather than in a scratch folder that will be long
gone by then.

Run it LOCALLY: it needs a browser, and the redirect URI is a loopback address.
The server never runs this — it only ever needs the resulting refresh token,
which is why production needs no redirect URI, no browser, and no HTTPS.

    python manage.py spotify_authorize

Afterwards, the new token is written into the local .env. To update production,
copy that value into /etc/anotiontoponder.env and restart the collector timer —
see the re-auth runbook in the scope.
"""

import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from listening.models import SpotifyAuth, fingerprint_token
from listening.spotify import SCOPES, SpotifyError, build_authorize_url, exchange_code

ENV_FILENAME = ".env"


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches Spotify's redirect so the code never has to be pasted by hand."""

    captured = {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        expected_path = urllib.parse.urlparse(settings.SPOTIFY_REDIRECT_URI).path
        if parsed.path != expected_path:
            self.send_response(404)
            self.end_headers()
            return

        _CallbackHandler.captured = {
            key: values[0]
            for key, values in urllib.parse.parse_qs(parsed.query).items()
        }
        succeeded = "code" in _CallbackHandler.captured

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = (
            "<h2>Authorized.</h2><p>Close this tab and return to the terminal.</p>"
            if succeeded
            else f"<h2>Authorization failed</h2><pre>{_CallbackHandler.captured}</pre>"
        )
        self.wfile.write(
            f"<html><body style='font-family:sans-serif;padding:2rem'>{body}</body></html>".encode()
        )

    def log_message(self, *args):
        pass  # suppress the default request logging


class Command(BaseCommand):
    help = "Authorize this app against Spotify and store the refresh token (run locally)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--show",
            action="store_true",
            help="Print the refresh token in full. Off by default so the secret "
                 "stays out of terminal scrollback; use when you need to copy it "
                 "to the server env file.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=300,
            help="Seconds to wait for the browser redirect (default: 300).",
        )

    def handle(self, *args, **options):
        if not settings.SPOTIFY_CLIENT_ID or not settings.SPOTIFY_CLIENT_SECRET:
            raise CommandError(
                "SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET are not set.\n"
                "Add them to .env, then re-run. (Get them from the Spotify "
                "developer dashboard for the app you registered.)"
            )

        redirect_uri = settings.SPOTIFY_REDIRECT_URI
        parsed_redirect = urllib.parse.urlparse(redirect_uri)
        if parsed_redirect.hostname == "localhost":
            raise CommandError(
                f"SPOTIFY_REDIRECT_URI is {redirect_uri!r}, but Spotify prohibits "
                "'localhost'. Use an explicit loopback IP, e.g. "
                "http://127.0.0.1:8888/callback — and make sure the same value is "
                "registered in the dashboard."
            )
        port = parsed_redirect.port or 80

        self.stdout.write(self.style.MIGRATE_HEADING("Spotify authorization"))
        self.stdout.write(f"  redirect uri : {redirect_uri}")
        self.stdout.write(f"  scopes       : {' '.join(SCOPES)}")

        # Echoed back by Spotify; comparing it proves the callback belongs to the
        # flow we started rather than something else hitting the local port.
        state = secrets.token_urlsafe(16)
        authorize_url = build_authorize_url(state)

        self.stdout.write("\nOpening your browser to approve access...")
        self.stdout.write("If it does not open, paste this URL manually:\n")
        self.stdout.write(f"  {authorize_url}\n")
        webbrowser.open(authorize_url)

        self.stdout.write(f"Waiting up to {options['timeout']}s for the redirect...")
        server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
        server.timeout = options["timeout"]
        try:
            server.handle_request()
        finally:
            server.server_close()

        captured = _CallbackHandler.captured
        if not captured:
            raise CommandError("No callback received before the timeout.")
        if "error" in captured:
            raise CommandError(
                f"Spotify returned: {captured['error']}\n"
                "If this mentions the redirect URI, confirm the exact value is "
                "registered in the dashboard (and that you clicked 'Add')."
            )
        if captured.get("state") != state:
            raise CommandError("State mismatch — discarding this callback.")

        self.stdout.write("Exchanging the authorization code for tokens...")
        try:
            payload = exchange_code(captured["code"])
        except SpotifyError as exc:
            raise CommandError(str(exc))

        refresh_token = payload.get("refresh_token")
        if not refresh_token:
            raise CommandError(
                f"No refresh_token in the response (keys: {sorted(payload)}). "
                "Without it, unattended collection is impossible."
            )

        granted_scopes = payload.get("scope", "")

        # Record the authorization so the admin can count down to expiry. Only a
        # fingerprint is stored — never the secret itself, because the database
        # gets dumped and copied around.
        auth = SpotifyAuth.load()
        auth.refresh_token_fingerprint = fingerprint_token(refresh_token)
        auth.authorized_at = timezone.now()
        auth.scopes = granted_scopes
        auth.access_token = payload.get("access_token", "")
        auth.access_token_expires_at = timezone.now() + timezone.timedelta(
            seconds=payload.get("expires_in", 3600)
        )
        auth.save()

        env_path = Path(settings.BASE_DIR).parent / ENV_FILENAME
        wrote_to_env = self._upsert_env(env_path, "SPOTIFY_REFRESH_TOKEN", refresh_token)

        self.stdout.write(self.style.SUCCESS("\nAuthorized."))
        self.stdout.write(f"  granted scopes : {granted_scopes}")
        self.stdout.write(f"  expires        : {auth.refresh_token_expires_at:%Y-%m-%d} "
                          f"(~{auth.days_until_reauth} days)")

        if wrote_to_env:
            self.stdout.write(f"  refresh token  : written to {env_path}")
        else:
            self.stdout.write(self.style.WARNING(
                f"  could not write {env_path} — set SPOTIFY_REFRESH_TOKEN yourself"
            ))

        if options["show"]:
            self.stdout.write(f"\n  SPOTIFY_REFRESH_TOKEN={refresh_token}")
        else:
            self.stdout.write(
                "\n  (token not printed. Re-run with --show, or read it from .env, "
                "when you need to copy it to the server.)"
            )

        self.stdout.write(self.style.MIGRATE_HEADING("\nTo update production"))
        self.stdout.write(
            "  1. Copy the SPOTIFY_REFRESH_TOKEN value into /etc/anotiontoponder.env\n"
            "  2. Restart/re-run the collector timer on the server\n"
            "  Easy to forget step 2 — prod stays dead while local looks healthy."
        )

    def _upsert_env(self, path, key, value):
        """Add or replace KEY=value in .env, leaving everything else intact."""
        if not path.exists():
            return False
        lines = path.read_text(encoding="utf-8").splitlines()
        output, replaced = [], False
        for line in lines:
            if line.startswith(f"{key}="):
                output.append(f"{key}={value}")
                replaced = True
            else:
                output.append(line)
        if not replaced:
            if output and output[-1].strip():
                output.append("")
            output.append("# Written by manage.py spotify_authorize")
            output.append(f"{key}={value}")
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
        return True
