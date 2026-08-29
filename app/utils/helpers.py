"""
app/utils/helpers.py
────────────────────
Pure utility functions — no FastAPI or LinkedIn-specific imports.
Handles URL normalisation, slug extraction, and cookie sanitisation.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse


# ── LinkedIn URL helpers ─────────────────────────────────────────────────────

# Matches /in/<slug> with optional trailing query-string / fragment
_LINKEDIN_SLUG_RE = re.compile(
    r"linkedin\.com/in/([A-Za-z0-9\-_%]+)",
    re.IGNORECASE,
)


def extract_linkedin_slug(url: str) -> str | None:
    """
    Extract the profile slug (public identifier) from a LinkedIn profile URL.

    Examples
    --------
    >>> extract_linkedin_slug("https://www.linkedin.com/in/satyanadella")
    'satyanadella'
    >>> extract_linkedin_slug("https://www.linkedin.com/in/john-doe-123?trk=nav")
    'john-doe-123'
    >>> extract_linkedin_slug("not-a-linkedin-url")
    None
    """
    match = _LINKEDIN_SLUG_RE.search(url)
    if not match:
        return None
    # Strip trailing slash or query params that crept into the capture group
    return match.group(1).rstrip("/").split("?")[0].split("#")[0]


def normalize_linkedin_url(url: str) -> str:
    """
    Return a canonical, clean LinkedIn profile URL.

    Strips query parameters, fragments, and trailing slashes so we always
    hit LinkedIn with a consistent identifier.

    Examples
    --------
    >>> normalize_linkedin_url("https://www.linkedin.com/in/satyanadella?trk=nav_responsive_tab_profile")
    'https://www.linkedin.com/in/satyanadella'
    """
    parsed = urlparse(url.strip())
    # Force scheme and netloc to canonical form
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.linkedin.com"
    # Remove query string and fragment, normalise path
    path = parsed.path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def is_valid_linkedin_profile_url(url: str) -> bool:
    """Return True if *url* looks like a LinkedIn /in/ profile URL."""
    return bool(_LINKEDIN_SLUG_RE.search(url))


# ── Cookie / credential helpers ──────────────────────────────────────────────

def sanitize_cookie_value(value: str) -> str:
    """
    Strip surrounding whitespace and double-quotes from a cookie value.

    LinkedIn cookies copied from DevTools sometimes include literal quotes:
        "ajax:1234567890"  →  ajax:1234567890

    >>> sanitize_cookie_value('"ajax:1234567890"')
    'ajax:1234567890'
    >>> sanitize_cookie_value("  ajax:1234567890  ")
    'ajax:1234567890'
    """
    return value.strip().strip('"')


def extract_csrf_token(jsessionid: str) -> str:
    """
    LinkedIn expects the `csrf-token` header to equal the raw JSESSIONID
    value (without surrounding quotes).

    >>> extract_csrf_token('"ajax:1234567890"')
    'ajax:1234567890'
    """
    return sanitize_cookie_value(jsessionid)


# ── Date helpers ─────────────────────────────────────────────────────────────

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def month_number_to_name(month: int | None) -> str | None:
    """Convert a 1-based month integer to its English name, or return None."""
    if month is None:
        return None
    if 1 <= month <= 12:
        return _MONTH_NAMES[month - 1]
    return None


# ── LinkedIn internal URN helpers ─────────────────────────────────────────────

_URN_ID_RE = re.compile(r":(\d+)$")


def urn_to_id(urn: str | None) -> str | None:
    """
    Extract the numeric ID from a LinkedIn URN string.

    >>> urn_to_id("urn:li:fsd_profile:ACoAABxxxxxx")
    None  # non-numeric; some URNs contain base64 IDs
    >>> urn_to_id("urn:li:company:12345")
    '12345'
    """
    if not urn:
        return None
    match = _URN_ID_RE.search(urn)
    return match.group(1) if match else None


def build_company_url(company_universal_name: str | None) -> str | None:
    """Return a LinkedIn company URL from the universalName field."""
    if not company_universal_name:
        return None
    return f"https://www.linkedin.com/company/{company_universal_name}"


def build_school_url(school_universal_name: str | None) -> str | None:
    """Return a LinkedIn school URL from the universalName field."""
    if not school_universal_name:
        return None
    return f"https://www.linkedin.com/school/{school_universal_name}"


# ── Image URL helpers ─────────────────────────────────────────────────────────

def build_profile_image_url(
    root_url: str | None,
    artifacts: list[dict] | None,
    prefer_width: int = 400,
) -> str | None:
    """
    Construct the best-quality profile image URL from Voyager's
    vectorImage structure.

    Voyager image structure:
        {
          "rootUrl": "https://media.licdn.com/dms/image/...",
          "artifacts": [
            {"width": 100, "height": 100, "fileIdentifyingUrlPathSegment": "100_100/..."},
            {"width": 200, "height": 200, "fileIdentifyingUrlPathSegment": "200_200/..."},
            ...
          ]
        }

    Selects the artifact whose width is closest to *prefer_width*.
    """
    if not root_url or not artifacts:
        return None

    best: dict | None = None
    best_diff = float("inf")
    for artifact in artifacts:
        w = artifact.get("width", 0)
        diff = abs(w - prefer_width)
        if diff < best_diff:
            best_diff = diff
            best = artifact

    if best is None:
        return None

    segment = best.get("fileIdentifyingUrlPathSegment", "")
    return f"{root_url.rstrip('/')}/{segment.lstrip('/')}"
