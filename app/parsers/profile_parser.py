"""
app/parsers/profile_parser.py
──────────────────────────────
Voyager normalised JSON → ProfileResponse

LinkedIn's Voyager API returns data in two formats depending on the endpoint:

1. **Dash** (`/voyager/api/identity/dash/profiles`):
   Returns `{ "data": {...}, "included": [...] }`.
   The `included` array contains a flat list of typed graph objects
   (`$type` discriminator field).

2. **Classic** (`/voyager/api/identity/profiles/{username}/profileView`):
   Returns a nested `{ "data": { "profile": {...}, ... } }` structure.

This module handles **both** formats automatically by detecting which
shape was received.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.schemas.profile import (
    CertificationItem,
    DateRange,
    EducationItem,
    ExperienceItem,
    LanguageItem,
    ProfilePicture,
    ProfileResponse,
    SkillItem,
)
from app.utils.helpers import (
    build_company_url,
    build_profile_image_url,
    build_school_url,
)

logger = logging.getLogger(__name__)


# ── Public entry point ───────────────────────────────────────────────────────


def parse_profile(raw: dict[str, Any], username: str) -> ProfileResponse:
    """
    Parse raw Voyager API JSON into a structured :class:`ProfileResponse`.

    Automatically detects whether the payload is from the Dash endpoint
    (contains an `included` key) or the Classic endpoint.

    Parameters
    ----------
    raw:
        The raw JSON dict returned by :class:`~app.client.linkedin.LinkedInClient`.
    username:
        The LinkedIn slug, used to build the canonical profile URL and as
        fallback for the `public_identifier` field.

    Returns
    -------
    ProfileResponse
        Fully populated (best-effort) profile data object.
    """
    scraped_at = datetime.now(tz=timezone.utc).isoformat()

    if "included" in raw:
        # ── Dash / normalised format ──────────────────────────────────────
        profile = _parse_dash_format(raw, username)
    else:
        # ── Classic profileView format ────────────────────────────────────
        profile = _parse_classic_format(raw, username)

    profile.scraped_at = scraped_at
    profile.linkedin_url = f"https://www.linkedin.com/in/{username}"
    if not profile.public_identifier:
        profile.public_identifier = username

    return profile


# ── Dash format parser ───────────────────────────────────────────────────────

def _parse_dash_format(raw: dict[str, Any], username: str) -> ProfileResponse:
    """Parse the Dash endpoint's normalised graph JSON."""
    included: list[dict] = raw.get("included", [])

    # Index all objects by their `$type` for fast lookup
    type_map: dict[str, list[dict]] = {}
    for obj in included:
        t = obj.get("$type", "")
        type_map.setdefault(t, []).append(obj)

    # ── Find the main profile object ──────────────────────────────────────
    profile_obj: dict[str, Any] = {}
    for candidate_type in [
        "com.linkedin.voyager.dash.identity.profile.Profile",
        "com.linkedin.voyager.identity.shared.MiniProfile",
    ]:
        candidates = type_map.get(candidate_type, [])
        for c in candidates:
            if c.get("publicIdentifier", "").lower() == username.lower():
                profile_obj = c
                break
        if profile_obj:
            break
    # Fallback: take any profile-shaped object with a publicIdentifier
    if not profile_obj:
        for obj in included:
            if obj.get("publicIdentifier"):
                profile_obj = obj
                break

    if not profile_obj:
        logger.warning("Could not locate main profile object in dash response.")

    # ── Basic info ────────────────────────────────────────────────────────
    first_name = profile_obj.get("firstName", "")
    last_name = profile_obj.get("lastName", "")
    full_name = f"{first_name} {last_name}".strip() or None

    # Location lives in a nested geo sub-object or at the top level
    location_name = (
        profile_obj.get("geoLocationName")
        or _nested(profile_obj, "geoLocation", "geo", "defaultLocalizedName")
        or profile_obj.get("locationName")
    )
    country_code = (
        _nested(profile_obj, "geoCountryName")
        or _nested(profile_obj, "location", "basicLocation", "countryCode")
    )

    # ── Profile picture ───────────────────────────────────────────────────
    picture_obj = profile_obj.get("profilePicture", {}) or {}
    display_image = picture_obj.get("displayImageReference", {}) or {}
    vector_image = display_image.get("vectorImage", {}) or {}

    # Also try alternative path in dash
    if not vector_image:
        for obj in included:
            if obj.get("$type", "").endswith("ProfilePicture"):
                vi = _nested(obj, "displayImageReference", "vectorImage")
                if vi:
                    vector_image = vi
                    break

    profile_picture: ProfilePicture | None = None
    if vector_image:
        root_url = vector_image.get("rootUrl")
        artifacts = vector_image.get("artifacts", [])
        img_url = build_profile_image_url(root_url, artifacts, prefer_width=400)
        if img_url:
            # Find the artifact closest to 400px for dimensions
            best = _closest_artifact(artifacts, 400)
            profile_picture = ProfilePicture(
                url=img_url,
                width=best.get("width") if best else None,
                height=best.get("height") if best else None,
            )

    # ── Network info ──────────────────────────────────────────────────────
    network_obj = next(
        (
            o for o in included
            if o.get("$type", "").endswith("NetworkInfo")
        ),
        {},
    )
    connection_count = network_obj.get("connectionsCount")
    follower_count = network_obj.get("followerCount")

    # ── Experience ────────────────────────────────────────────────────────
    experience: list[ExperienceItem] = []
    for obj in included:
        t = obj.get("$type", "")
        if not ("Position" in t or "WorkExperience" in t):
            continue
        exp = _parse_experience_item_dash(obj, type_map)
        if exp:
            experience.append(exp)

    # ── Education ─────────────────────────────────────────────────────────
    education: list[EducationItem] = []
    for obj in included:
        t = obj.get("$type", "")
        if "Education" not in t:
            continue
        edu = _parse_education_item_dash(obj, type_map)
        if edu:
            education.append(edu)

    # ── Skills ────────────────────────────────────────────────────────────
    skills: list[SkillItem] = []
    seen_skills: set[str] = set()
    for obj in included:
        t = obj.get("$type", "")
        if "Skill" not in t:
            continue
        name = obj.get("name") or _nested(obj, "skill", "name")
        if name and name not in seen_skills:
            seen_skills.add(name)
            endorsement_count = obj.get("endorsementCount")
            skills.append(SkillItem(name=name, endorsement_count=endorsement_count))

    # ── Certifications ────────────────────────────────────────────────────
    certifications: list[CertificationItem] = []
    for obj in included:
        t = obj.get("$type", "")
        if "Certification" not in t:
            continue
        cert = _parse_certification_item(obj)
        if cert:
            certifications.append(cert)

    # ── Languages ─────────────────────────────────────────────────────────
    languages: list[LanguageItem] = []
    for obj in included:
        t = obj.get("$type", "")
        if "Language" not in t:
            continue
        name = obj.get("name")
        if name:
            proficiency = (
                obj.get("proficiency")
                or _nested(obj, "proficiency", "level")
                or _nested(obj, "proficiencyLevel")
            )
            languages.append(LanguageItem(name=name, proficiency=proficiency))

    return ProfileResponse(
        public_identifier=profile_obj.get("publicIdentifier"),
        first_name=first_name or None,
        last_name=last_name or None,
        full_name=full_name,
        headline=profile_obj.get("headline"),
        summary=profile_obj.get("summary"),
        location_name=location_name,
        country_code=country_code,
        profile_picture=profile_picture,
        connection_count=connection_count,
        follower_count=follower_count,
        experience=experience,
        education=education,
        skills=skills,
        certifications=certifications,
        languages=languages,
    )


def _parse_experience_item_dash(obj: dict, type_map: dict[str, list]) -> ExperienceItem | None:
    """Extract an ExperienceItem from a Dash Position/WorkExperience object."""
    title = obj.get("title")
    if not title:
        return None

    company_name = (
        _nested(obj, "companyName")
        or _nested(obj, "company", "name")
    )
    company_universal_name = (
        _nested(obj, "companyUrn")  # sometimes the URN contains the name
        or _nested(obj, "company", "universalName")
    )
    company_linkedin_url = build_company_url(
        obj.get("companyPageUrl") or company_universal_name
    )
    company_logo_url = _extract_logo_url(obj, "company")

    date_range = _parse_date_range(obj.get("dateRange", {}))
    location = obj.get("locationName") or obj.get("location")
    employment_type = (
        obj.get("employmentType")
        or _nested(obj, "employmentTypeUrn")
    )
    description = obj.get("description")

    return ExperienceItem(
        company_name=company_name,
        company_linkedin_url=company_linkedin_url,
        company_logo_url=company_logo_url,
        title=title,
        employment_type=employment_type,
        location=location,
        description=description,
        date_range=date_range,
    )


def _parse_education_item_dash(obj: dict, type_map: dict[str, list]) -> EducationItem | None:
    """Extract an EducationItem from a Dash Education object."""
    school_name = (
        obj.get("schoolName")
        or _nested(obj, "school", "schoolName")
    )
    if not school_name:
        return None

    school_universal_name = _nested(obj, "school", "universalName")
    school_linkedin_url = build_school_url(school_universal_name)
    school_logo_url = _extract_logo_url(obj, "school")

    return EducationItem(
        school_name=school_name,
        school_linkedin_url=school_linkedin_url,
        school_logo_url=school_logo_url,
        degree_name=obj.get("degreeName"),
        field_of_study=obj.get("fieldOfStudy"),
        grade=obj.get("grade"),
        description=obj.get("description"),
        date_range=_parse_date_range(obj.get("dateRange", {})),
    )


# ── Classic format parser ────────────────────────────────────────────────────

def _parse_classic_format(raw: dict[str, Any], username: str) -> ProfileResponse:
    """Parse the classic `/profileView` endpoint's nested JSON."""
    data = raw.get("data", raw)  # some wrappers put it at root

    profile = data.get("profile", {}) or {}
    mini = profile.get("miniProfile", {}) or data.get("miniProfile", {}) or {}

    first_name = mini.get("firstName") or profile.get("firstName", "")
    last_name = mini.get("lastName") or profile.get("lastName", "")
    full_name = f"{first_name} {last_name}".strip() or None

    # Location
    location_name = (
        profile.get("locationName")
        or _nested(profile, "geoLocation", "geo", "defaultLocalizedName")
    )
    country_code = _nested(profile, "location", "basicLocation", "countryCode")

    # Profile picture
    picture_data = mini.get("picture", {}) or {}
    vector_image = picture_data.get("com.linkedin.common.VectorImage", {}) or {}
    profile_picture: ProfilePicture | None = None
    if vector_image:
        root_url = vector_image.get("rootUrl")
        artifacts = vector_image.get("artifacts", [])
        img_url = build_profile_image_url(root_url, artifacts, prefer_width=400)
        if img_url:
            best = _closest_artifact(artifacts, 400)
            profile_picture = ProfilePicture(
                url=img_url,
                width=best.get("width") if best else None,
                height=best.get("height") if best else None,
            )

    # Network info
    network_info = data.get("networkInfo", {}) or {}
    connection_count = network_info.get("connections", {}).get("total")
    follower_count = None  # not available in classic endpoint

    # Experience
    experience: list[ExperienceItem] = []
    for pos in data.get("positionView", {}).get("elements", []):
        title = pos.get("title")
        if not title:
            continue
        company = pos.get("company", {}) or {}
        company_name = pos.get("companyName") or company.get("name")
        company_universal_name = company.get("universalName")
        experience.append(
            ExperienceItem(
                company_name=company_name,
                company_linkedin_url=build_company_url(company_universal_name),
                title=title,
                employment_type=pos.get("employmentType"),
                location=pos.get("locationName"),
                description=pos.get("description"),
                date_range=_parse_classic_date_range(pos),
            )
        )

    # Education
    education: list[EducationItem] = []
    for edu in data.get("educationView", {}).get("elements", []):
        school_name = edu.get("schoolName")
        if not school_name:
            continue
        education.append(
            EducationItem(
                school_name=school_name,
                degree_name=edu.get("degreeName"),
                field_of_study=edu.get("fieldOfStudy"),
                grade=edu.get("grade"),
                description=edu.get("description"),
                date_range=_parse_classic_date_range(edu),
            )
        )

    # Skills
    skills: list[SkillItem] = []
    seen: set[str] = set()
    for sk in data.get("skillView", {}).get("elements", []):
        name = _nested(sk, "skill", "name") or sk.get("name")
        if name and name not in seen:
            seen.add(name)
            skills.append(SkillItem(name=name, endorsement_count=sk.get("endorsementCount")))

    # Certifications
    certifications: list[CertificationItem] = []
    for cert in data.get("certificationView", {}).get("elements", []):
        name = cert.get("name")
        certifications.append(
            CertificationItem(
                name=name,
                authority=cert.get("authority"),
                license_number=cert.get("licenseNumber"),
                url=cert.get("url"),
                date_range=_parse_classic_date_range(cert),
            )
        )

    # Languages
    languages: list[LanguageItem] = []
    for lang in data.get("languageView", {}).get("elements", []):
        name = lang.get("name")
        if name:
            languages.append(
                LanguageItem(name=name, proficiency=lang.get("proficiency"))
            )

    return ProfileResponse(
        public_identifier=mini.get("publicIdentifier") or username,
        first_name=first_name or None,
        last_name=last_name or None,
        full_name=full_name,
        headline=mini.get("occupation") or profile.get("headline"),
        summary=profile.get("summary"),
        location_name=location_name,
        country_code=country_code,
        profile_picture=profile_picture,
        connection_count=connection_count,
        follower_count=follower_count,
        experience=experience,
        education=education,
        skills=skills,
        certifications=certifications,
        languages=languages,
    )


# ── Shared parse helpers ─────────────────────────────────────────────────────

def _parse_date_range(dr: dict | None) -> DateRange | None:
    """
    Parse a Voyager dateRange object:
    { "start": {"year": 2020, "month": 1}, "end": {"year": 2023, "month": 6} }
    """
    if not dr:
        return None
    start = dr.get("start") or {}
    end = dr.get("end") or {}
    is_current = not bool(end)
    return DateRange(
        start_year=start.get("year"),
        start_month=start.get("month"),
        end_year=end.get("year"),
        end_month=end.get("month"),
        is_current=is_current,
    )


def _parse_classic_date_range(obj: dict) -> DateRange | None:
    """
    Parse classic profileView date ranges which come as:
    { "timePeriod": { "startDate": {"year": 2020, "month": 1}, "endDate": ... } }
    """
    tp = obj.get("timePeriod") or obj.get("dateRange") or {}
    if not tp:
        return None
    start = tp.get("startDate") or tp.get("start") or {}
    end = tp.get("endDate") or tp.get("end") or {}
    is_current = not bool(end)
    return DateRange(
        start_year=start.get("year"),
        start_month=start.get("month"),
        end_year=end.get("year"),
        end_month=end.get("month"),
        is_current=is_current,
    )


def _parse_certification_item(obj: dict) -> CertificationItem | None:
    """Parse a Dash Certification object."""
    name = obj.get("name")
    return CertificationItem(
        name=name,
        authority=obj.get("authority") or _nested(obj, "company", "name"),
        license_number=obj.get("licenseNumber"),
        url=obj.get("url"),
        date_range=_parse_date_range(obj.get("dateRange")),
    )


def _extract_logo_url(obj: dict, key: str) -> str | None:
    """
    Try to extract a company/school logo URL from nested structures like:
      obj[key]["logo"]["image"]["com.linkedin.common.VectorImage"]["rootUrl"]
    """
    sub = obj.get(key, {}) or {}
    logo = sub.get("logo", {}) or {}
    image = logo.get("image", {}) or {}
    vector = (
        image.get("com.linkedin.common.VectorImage")
        or image.get("vectorImage")
        or {}
    )
    root_url = vector.get("rootUrl")
    artifacts = vector.get("artifacts", [])
    return build_profile_image_url(root_url, artifacts, prefer_width=200)


def _closest_artifact(artifacts: list[dict], target_width: int) -> dict | None:
    """Return the artifact whose width is closest to *target_width*."""
    if not artifacts:
        return None
    return min(artifacts, key=lambda a: abs(a.get("width", 0) - target_width))


def _nested(obj: dict, *keys: str) -> Any:
    """
    Safe nested dict access.  Returns None if any key is missing or the
    intermediate value is not a dict.

    Example
    -------
    >>> _nested({"a": {"b": {"c": 1}}}, "a", "b", "c")
    1
    >>> _nested({"a": None}, "a", "b")
    None
    """
    current: Any = obj
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
    return current
