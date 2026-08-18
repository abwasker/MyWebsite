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


class SpotifyAuthError(SpotifyError):
    """401 — the access token was rejected. Caller should refresh and retry once."""


class SpotifyRateLimited(SpotifyError):
    """429 — back off. Carries Spotify's Retry-After (seconds) when supplied."""

    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__(
            f"Rate limited by Spotify (retry after {retry_after}s)"
            if retry_after is not None
            else "Rate limited by Spotify"
        )


class SpotifyUnavailable(SpotifyError):
    """Network failure or a 5xx. Transient by nature — the next poll retries."""


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
    try:
        response = requests.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers=_basic_auth_header(),
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        # Transient, and it must not look like a lapsed token: the collector runs
        # unattended, so a network blip has to be survivable rather than fatal.
        raise SpotifyUnavailable(f"Could not reach Spotify to refresh the token: {exc}") from exc

    if response.status_code >= 500:
        raise SpotifyUnavailable(f"Spotify token endpoint error ({response.status_code})")
    if response.status_code != 200:
        # invalid_grant here means revoked or lapsed — the silent-failure mode.
        raise SpotifyError(
            f"Refresh failed ({response.status_code}): {response.text}\n"
            "If this says invalid_grant, the refresh token has expired or been "
            "revoked. Re-run: manage.py spotify_authorize"
        )
    return response.json()


def _api_get(path, access_token, params=None):
    """GET an API endpoint, translating status codes into typed exceptions.

    Non-200 responses are this collector's *normal* control flow, not
    exceptional: 204 on nearly every poll (nothing playing), 401 every hour
    (token expiry), 429 under rate limiting. Giving each a distinct type lets the
    caller branch on the cause rather than parsing message strings.
    """
    try:
        response = requests.get(
            f"{API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        # No connection, DNS failure, timeout. On a timer this is a shrug, not a
        # failure — the next run picks up where this one left off.
        raise SpotifyUnavailable(f"Could not reach Spotify: {exc}") from exc

    if response.status_code == 204:
        # Nothing is playing. This is the majority of polls.
        return None
    if response.status_code == 200:
        # Some deployments answer "nothing playing" as a 200 with no body. Note
        # this is checked only for 200s: treating ANY empty body as "nothing
        # playing" would let a bodiless 401 masquerade as a silent quiet spell,
        # which is the one failure mode this project cannot afford to hide.
        return response.json() if response.content else None
    if response.status_code == 401:
        raise SpotifyAuthError(f"Access token rejected: {response.text}")
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        raise SpotifyRateLimited(int(retry_after) if retry_after and retry_after.isdigit() else None)
    if response.status_code >= 500:
        raise SpotifyUnavailable(f"Spotify server error ({response.status_code})")
    raise SpotifyError(f"GET {path} failed ({response.status_code}): {response.text}")


def get_currently_playing(access_token):
    """What is playing *right now*, or None if nothing is (HTTP 204).

    ``additional_types=episode`` is mandatory: without it Spotify returns null
    for podcast playback. This endpoint is the ONLY source of podcast plays that
    exists — ``recently-played`` is tracks-only — so every podcast row this app
    will ever hold comes from here.
    """
    return _api_get(
        "/me/player/currently-playing",
        access_token,
        {"additional_types": "episode"},
    )


def get_recently_played(access_token, limit=50):
    """The most recent track plays, each with an authoritative ``played_at``.

    Tracks only — the response wraps every entry as ``{context, played_at,
    track}``, with no slot an episode could occupy, and passing any other type
    is rejected with ``400 "Bad type field, must be any of [track]"``.

    No cursor is stored deliberately: refetching the full window every run costs
    nothing, self-heals after a missed run, and the unique constraint on
    ``PlayEvent`` discards the overlap.
    """
    payload = _api_get("/me/player/recently-played", access_token, {"limit": limit})
    if not payload:
        return []
    return payload.get("items", [])
