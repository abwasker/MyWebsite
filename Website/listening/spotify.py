"""Spotify Web API client — the only module that knows this vendor exists.

Keeping vendor specifics here means a second source (a different podcast app, or
an imported export) can be added later without touching the models or the views.

Verified facts this module is built on (Phase 0 probe, 2026-08-10):

* ``/me/player/currently-playing?additional_types=episode`` is the ONLY source of
  podcast plays. ``recently-played`` is tracks-only and rejects any other type
  with ``400 "Bad type field, must be any of [track]"``.
* No endpoint publishes ``ms_played``, so listening time is always an estimate.
* An episode's "creator" comes from ``show.name``; ``show.publisher`` is absent.
* Access tokens last 1 hour. Refresh tokens last ~6 months and refreshing does
  NOT extend that.
"""

import base64

import requests
from django.conf import settings

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

# Read-only, and deliberately the minimum that does the job. Notably absent:
# user-library-read — we don't need saved shows, so we don't ask for them.
SCOPES = (
    "user-read-currently-playing",   # the only window into podcast plays
    "user-read-recently-played",     # 50 recent TRACKS with authoritative played_at
    "user-read-playback-position",   # resume_point on episodes
)

TIMEOUT = 30


class SpotifyError(RuntimeError):
    """Raised for API failures that callers are not expected to handle."""


def _basic_auth_header():
    client_id = settings.SPOTIFY_CLIENT_ID
    client_secret = settings.SPOTIFY_CLIENT_SECRET
    if not client_id or not client_secret:
        raise SpotifyError(
            "SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET are not set. "
            "Add them to the .env file (locally) or /etc/anotiontoponder.env (server)."
        )
    token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def build_authorize_url(state):
    """URL the user visits once to grant access."""
    from urllib.parse import urlencode

    query = urlencode({
        "client_id": settings.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        # Always show consent, so a scope change genuinely re-prompts instead of
        # silently reusing an older, narrower grant.
        "show_dialog": "true",
    })
    return f"{AUTH_URL}?{query}"


def exchange_code(code):
    """Trade a one-time authorization code for tokens. Includes the refresh token."""
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
        },
        headers=_basic_auth_header(),
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise SpotifyError(f"Token exchange failed ({response.status_code}): {response.text}")
    return response.json()


def refresh_access_token(refresh_token):
    """Mint a fresh 1-hour access token.

    This is the whole reason the server needs no browser and no redirect URI:
    given the refresh token, access renews unattended.
    """
    response = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers=_basic_auth_header(),
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        # invalid_grant here means revoked or lapsed — the silent-failure mode.
        raise SpotifyError(
            f"Refresh failed ({response.status_code}): {response.text}\n"
            "If this says invalid_grant, the refresh token has expired or been "
            "revoked. Re-run: manage.py spotify_authorize"
        )
    return response.json()
