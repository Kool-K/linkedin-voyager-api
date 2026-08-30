"""
app/main.py
───────────
FastAPI application entrypoint.

Responsibilities
────────────────
* Application factory with lifespan context manager.
* Global middleware (CORS, request timing, structured error handling).
* Dependency injection of LinkedInClient.
* Route definitions for health-check and profile endpoints.
* Custom OpenAPI metadata and Swagger UI configuration.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncGenerator

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.client.linkedin import LinkedInClient
from app.config import Settings, get_settings
from app.parsers.profile_parser import parse_profile
from app.schemas.profile import (
    ErrorDetail,
    HealthResponse,
    ProfileRequest,
    ProfileResponse,
)
from app.utils.helpers import extract_linkedin_slug, is_valid_linkedin_profile_url

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage the lifecycle of shared resources.

    On startup : validate credentials and create the LinkedIn HTTP client.
    On shutdown: gracefully close the connection pool.
    """
    settings = get_settings()

    # Configure log level from settings
    logging.getLogger().setLevel(settings.log_level)

    logger.info(
        "Starting LinkedIn Voyager API | env=%s | timeout=%.1fs | max_conns=%d",
        settings.app_env,
        settings.request_timeout,
        settings.max_connections,
    )

    # Validate credentials are present (raises ValueError if misconfigured)
    try:
        _ = settings.csrf_token  # triggers computed property + validator
    except Exception as exc:
        logger.error("Credential validation failed: %s", exc)
        raise

    client = LinkedInClient(settings)
    app.state.linkedin_client = client
    app.state.settings = settings

    logger.info("LinkedIn HTTP client initialised. Ready to serve requests.")

    yield  # ── Application runs here ──────────────────────────────────────

    logger.info("Shutting down — closing LinkedIn HTTP client.")
    await client.aclose()


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="LinkedIn Voyager Profile Scraper API",
        description=(
            "Production-grade, browser-less LinkedIn profile scraper that reverse-engineers "
            "LinkedIn's internal Voyager REST API to return rich, structured profile data.\n\n"
            "**Authentication**: Requires valid `li_at` and `JSESSIONID` cookies from an active "
            "LinkedIn browser session, configured via environment variables."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={
            "name": "API Support",
            "email": "support@example.com",
        },
        license_info={
            "name": "MIT",
        },
    )

    # ── CORS middleware ───────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten this in production
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # ── Request timing middleware ─────────────────────────────────────────
    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next: Any) -> Any:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        response.headers["X-Process-Time"] = f"{elapsed:.4f}s"
        return response

    # ── Global exception handlers ─────────────────────────────────────────
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorDetail(
                error=_status_code_to_error_name(exc.status_code),
                message=str(exc.detail),
            ).model_dump(exclude_none=True),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=ErrorDetail(
                error="internal_server_error",
                message="An unexpected internal error occurred. Please try again later.",
                detail=str(exc) if __debug__ else None,
            ).model_dump(exclude_none=True),
        )

    # ── Register routes ───────────────────────────────────────────────────
    _register_routes(app)

    return app


def _status_code_to_error_name(code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_server_error",
        502: "bad_gateway",
        503: "service_unavailable",
    }.get(code, "error")


# ── Dependency injection ──────────────────────────────────────────────────────

def get_client(request: Request) -> LinkedInClient:
    """FastAPI dependency: return the shared LinkedInClient instance."""
    return request.app.state.linkedin_client  # type: ignore[attr-defined]


def get_app_settings(request: Request) -> Settings:
    """FastAPI dependency: return the cached Settings instance."""
    return request.app.state.settings  # type: ignore[attr-defined]


ClientDep = Annotated[LinkedInClient, Depends(get_client)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


# ── Core scraping logic (shared by GET and POST) ──────────────────────────────

_profile_cache: dict[str, tuple[float, ProfileResponse]] = {}
CACHE_TTL = 60.0

async def _scrape_profile(url: str, client: LinkedInClient) -> ProfileResponse:
    """
    Shared implementation: validate URL → extract slug → fetch → parse.

    Parameters
    ----------
    url:
        Raw LinkedIn profile URL from the caller.
    client:
        Injected LinkedInClient.

    Returns
    -------
    ProfileResponse
    """
    if not is_valid_linkedin_profile_url(url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid LinkedIn profile URL: {url!r}. "
                "URL must contain '/in/', e.g. https://www.linkedin.com/in/username"
            ),
        )

    slug = extract_linkedin_slug(url)
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not extract a username/slug from URL: {url!r}",
        )

    now = time.time()
    if slug in _profile_cache:
        timestamp, cached_profile = _profile_cache[slug]
        if now - timestamp < CACHE_TTL:
            logger.info("Serving profile from cache | slug=%s", slug)
            return cached_profile

    logger.info("Scraping profile | slug=%s | url=%s", slug, url)
    raw_data = await client.fetch_profile(slug)
    profile = parse_profile(raw_data, slug)

    _profile_cache[slug] = (now, profile)

    logger.info(
        "Profile scraped | slug=%s | name=%s | exp=%d | edu=%d | skills=%d",
        slug,
        profile.full_name,
        len(profile.experience),
        len(profile.education),
        len(profile.skills),
    )
    return profile


# ── Route registration ────────────────────────────────────────────────────────

def _register_routes(app: FastAPI) -> None:

    # ── Health check ──────────────────────────────────────────────────────
    @app.get(
        "/",
        response_model=HealthResponse,
        summary="Health Check",
        description="Returns service status and version. Use this to verify the API is running.",
        tags=["Health"],
        operation_id="health_check",
    )
    async def health_check(settings: SettingsDep) -> HealthResponse:
        return HealthResponse(environment=settings.app_env)

    # ── POST /api/v1/profile ──────────────────────────────────────────────
    @app.post(
        "/api/v1/profile",
        response_model=ProfileResponse,
        summary="Scrape LinkedIn Profile (POST)",
        description=(
            "Fetch and parse a LinkedIn profile by providing the profile URL in the request body.\n\n"
            "**Example body**: `{\"url\": \"https://www.linkedin.com/in/satyanadella\"}`\n\n"
            "**Returns**: Structured profile data including experience, education, skills, and more."
        ),
        tags=["Profile"],
        operation_id="scrape_profile_post",
        responses={
            200: {"description": "Profile data successfully retrieved and parsed."},
            400: {"model": ErrorDetail, "description": "Invalid LinkedIn URL format."},
            404: {"model": ErrorDetail, "description": "Profile not found or private."},
            500: {"model": ErrorDetail, "description": "Credentials not configured."},
            502: {"model": ErrorDetail, "description": "LinkedIn blocked request or session expired."},
        },
    )
    async def scrape_profile_post(
        body: ProfileRequest,
        client: ClientDep,
    ) -> ProfileResponse:
        return await _scrape_profile(body.url, client)

    # ── GET /api/v1/profile ───────────────────────────────────────────────
    @app.get(
        "/api/v1/profile",
        response_model=ProfileResponse,
        summary="Scrape LinkedIn Profile (GET)",
        description=(
            "Fetch and parse a LinkedIn profile using a query parameter. "
            "Convenient for browser testing and curl.\n\n"
            "**Example**: `GET /api/v1/profile?url=https://www.linkedin.com/in/satyanadella`"
        ),
        tags=["Profile"],
        operation_id="scrape_profile_get",
        responses={
            200: {"description": "Profile data successfully retrieved and parsed."},
            400: {"model": ErrorDetail, "description": "Invalid LinkedIn URL format."},
            404: {"model": ErrorDetail, "description": "Profile not found or private."},
            500: {"model": ErrorDetail, "description": "Credentials not configured."},
            502: {"model": ErrorDetail, "description": "LinkedIn blocked request or session expired."},
        },
    )
    async def scrape_profile_get(
        client: ClientDep,
        url: str = Query(
            ...,
            description="LinkedIn profile URL, e.g. https://www.linkedin.com/in/satyanadella",
            examples={"satya": {"value": "https://www.linkedin.com/in/satyanadella"}},
        ),
    ) -> ProfileResponse:
        return await _scrape_profile(url, client)


# ── Application instance ──────────────────────────────────────────────────────

app = create_app()


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
