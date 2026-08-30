"""
app/client/linkedin.py
──────────────────────
Core async HTTP client for LinkedIn's Voyager internal REST API.

Key design decisions
────────────────────
* Uses httpx with HTTP/2 support and a shared connection pool (one client
  per application lifetime, managed via FastAPI lifespan).
* All requests are made with real browser-like headers to minimise the
  chance of bot-detection blocks.
* Two endpoints are attempted in order:
    1. Dash / FullProfileWithEntities (returns rich normalised JSON)
    2. Classic profileView (fallback)
* Raises structured FastAPI HTTPExceptions so callers don't need to
  handle raw httpx errors.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.config import Settings

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

LINKEDIN_BASE_URL = "https://www.linkedin.com"

# Primary Voyager Dash endpoint (returns normalised graph JSON with `included` array)
DASH_PROFILE_ENDPOINT = (
    "/voyager/api/identity/dash/profiles"
    "?q=memberIdentity"
    "&memberIdentity={username}"
    "&decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93"
)

# Fallback classic endpoint
CLASSIC_PROFILE_ENDPOINT = "/voyager/api/identity/profiles/{username}/profileView"

# Rotating list of realistic User-Agent strings to reduce fingerprinting
_USER_AGENTS = [
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
]


def _build_headers(settings: Settings) -> dict[str, str]:
    """Construct the exact set of HTTP request headers LinkedIn expects."""
    return {
        "User-Agent": _USER_AGENTS[0],
        "Accept": "application/vnd.linkedin.normalized+json+2.1",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "x-restli-protocol-version": "2.0.0",
        "x-li-lang": "en_US",
        "x-li-track": (
            '{"clientVersion":"1.13.10220","mpVersion":"1.13.10220",'
            '"osName":"web","timezoneOffset":5.5,"timezone":"Asia/Kolkata",'
            '"deviceFormFactor":"DESKTOP","mpName":"voyager-web","displayDensity":2,'
            '"displayWidth":1920,"displayHeight":1080}'
        ),
        "csrf-token": settings.csrf_token,
        "Referer": "https://www.linkedin.com/feed/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
    }


def _build_cookies(settings: Settings) -> dict[str, str]:
    """Build the minimal cookie jar LinkedIn needs to authenticate requests."""
    return {
        "li_at": settings.linkedin_li_at,
        "JSESSIONID": f'"{settings.csrf_token}"',
        "lang": "v=2&lang=en-us",
    }


# ── Client lifecycle ─────────────────────────────────────────────────────────

class LinkedInClient:
    """
    Async HTTP client wrapper for the LinkedIn Voyager API.

    Lifecycle
    ---------
    Create one instance per app lifetime.  Call `await client.aclose()` on
    shutdown to gracefully close the connection pool.

    Usage
    -----
    ```python
    client = LinkedInClient(settings)
    data = await client.fetch_profile("satyanadella")
    await client.aclose()
    ```
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        limits = httpx.Limits(
            max_connections=settings.max_connections,
            max_keepalive_connections=settings.max_connections // 2,
            keepalive_expiry=30,
        )
        self._client = httpx.AsyncClient(
            base_url=LINKEDIN_BASE_URL,
            headers=_build_headers(settings),
            cookies=_build_cookies(settings),
            limits=limits,
            timeout=httpx.Timeout(settings.request_timeout),
            http2=True,
            follow_redirects=True,
            max_redirects=5,
        )

    async def aclose(self) -> None:
        """Close the underlying httpx connection pool."""
        await self._client.aclose()

    # ── Public API ────────────────────────────────────────────────────────

    async def fetch_profile(self, username: str) -> dict[str, Any]:
        """
        Fetch raw Voyager JSON for *username*.

        Tries the primary Dash endpoint first; falls back to the classic
        profileView endpoint on 404 / parsing errors.

        Parameters
        ----------
        username:
            LinkedIn public identifier (slug), e.g. "satyanadella".

        Returns
        -------
        dict
            Raw Voyager API response dict.

        Raises
        ------
        HTTPException 400  – invalid username
        HTTPException 404  – profile not found
        HTTPException 502  – LinkedIn blocked / CAPTCHA / session expired
        HTTPException 500  – unexpected upstream error
        """
        if not username or not username.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="LinkedIn username/slug must not be empty.",
            )

        username = username.strip().lower()
        logger.info("Fetching LinkedIn profile for: %s", username)

        # ── Attempt 1: Dash endpoint ──────────────────────────────────────
        try:
            url = DASH_PROFILE_ENDPOINT.format(username=username)
            response = await self._client.get(url)
            logger.debug("Dash endpoint → HTTP %s", response.status_code)
            data = self._handle_response(response, username, endpoint="dash")
            if data:
                return data
        except HTTPException:
            raise
        except httpx.RequestError as exc:
            logger.warning("Network error on dash endpoint: %s", exc)

        # ── Attempt 2: Classic profileView fallback ───────────────────────
        logger.info("Falling back to classic profileView for: %s", username)
        try:
            url = CLASSIC_PROFILE_ENDPOINT.format(username=username)
            response = await self._client.get(url)
            logger.debug("Classic endpoint → HTTP %s", response.status_code)
            return self._handle_response(response, username, endpoint="classic")  # type: ignore[return-value]
        except HTTPException:
            raise
        except httpx.RequestError as exc:
            logger.error("Network error on classic endpoint: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Network error while contacting LinkedIn API: {exc}. "
                    "Check your internet connection and retry."
                ),
            ) from exc

    # ── Internal helpers ──────────────────────────────────────────────────

    def _handle_response(
        self,
        response: httpx.Response,
        username: str,
        endpoint: str,
    ) -> dict[str, Any] | None:
        """
        Inspect the HTTP response and raise appropriate HTTPExceptions.

        Returns the parsed JSON dict on success, or None to signal the
        caller to attempt the fallback endpoint.
        """
        code = response.status_code

        # ── Authwall / Checkpoint Detection ───────────────────────────────
        final_url = str(response.url).lower()
        if "authwall" in final_url or "checkpoint" in final_url or "login" in final_url:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="LinkedIn authentication challenge/checkpoint encountered. Please refresh session cookies.",
            )

        for history_response in response.history:
            h_url = str(history_response.url).lower()
            if "authwall" in h_url or "checkpoint" in h_url or "login" in h_url:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="LinkedIn authentication challenge/checkpoint encountered. Please refresh session cookies.",
                )

        # ── Success ───────────────────────────────────────────────────────
        if code == 200:
            try:
                data: dict[str, Any] = response.json()
            except Exception as exc:
                logger.warning("[%s] Failed to parse JSON response: %s", endpoint, exc)
                return None

            # Detect CAPTCHA / challenge pages served as 200 OK
            if self._is_challenge_response(data, response):
                logger.warning("[%s] LinkedIn returned a challenge page.", endpoint)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "LinkedIn returned a CAPTCHA or challenge page. "
                        "Your session cookie (li_at) may be expired or your IP "
                        "is temporarily rate-limited. "
                        "Please refresh your cookies and try again."
                    ),
                )

            return data

        # ── Not found ─────────────────────────────────────────────────────
        if code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"LinkedIn profile not found for username '{username}'. "
                    "The profile may be private, deleted, or the username is incorrect."
                ),
            )

        # ── Unauthorised / forbidden → treat as session expired ───────────
        if code in (401, 403):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"LinkedIn returned HTTP {code}. "
                    "Your session credentials (li_at / JSESSIONID) are invalid or expired. "
                    "Please log in to LinkedIn in a browser, copy fresh cookie values, "
                    "and update your .env file."
                ),
            )

        # ── Rate limited ──────────────────────────────────────────────────
        if code == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"LinkedIn rate-limited this request (HTTP 429). "
                    f"Retry-After: {retry_after} seconds. "
                    "LinkedIn rate limits have been temporarily reached. Reduce request frequency or rotate credentials."
                ),
            )

        # ── Other 4xx / 5xx ───────────────────────────────────────────────
        if code >= 500:
            logger.error("[%s] LinkedIn server error: HTTP %s", endpoint, code)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LinkedIn upstream server error (HTTP {code}). Please retry later.",
            )

        # For unexpected 4xx codes, log and return None to trigger fallback
        logger.warning("[%s] Unexpected HTTP %s — trying fallback.", endpoint, code)
        return None

    @staticmethod
    def _is_challenge_response(data: dict[str, Any], response: httpx.Response) -> bool:
        """
        Heuristic detection of LinkedIn challenge / CAPTCHA responses
        that are served with HTTP 200 but contain no profile data.
        """
        # LinkedIn sometimes returns an HTML challenge page as 200
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            return True

        # Voyager challenge JSON contains a 'status' field set to specific codes
        status_val = data.get("status")
        if isinstance(status_val, int) and status_val in (401, 403, 999):
            return True

        # Empty or minimal response likely means no auth
        if not data or (not data.get("data") and not data.get("included") and not data.get("elements")):
            # Classic profileView wraps everything in 'data'
            if "miniProfile" not in str(data)[:500]:
                return True

        return False
