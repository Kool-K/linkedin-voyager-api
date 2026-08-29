"""
app/schemas/profile.py
──────────────────────
Pydantic v2 models for the LinkedIn profile request/response contract.

All models use `model_config = ConfigDict(populate_by_name=True)` so they
work whether fields are set by alias or Python name.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


# ── Sub-models ──────────────────────────────────────────────────────────────


class DateRange(BaseModel):
    """Represents a year/month start-end range (e.g. for jobs or education)."""

    model_config = ConfigDict(populate_by_name=True)

    start_year: Optional[int] = Field(None, description="Start year.")
    start_month: Optional[int] = Field(None, ge=1, le=12, description="Start month (1–12).")
    end_year: Optional[int] = Field(None, description="End year. None = present.")
    end_month: Optional[int] = Field(None, ge=1, le=12, description="End month (1–12).")
    is_current: bool = Field(False, description="True when end date is absent (current role).")


class ExperienceItem(BaseModel):
    """A single entry in the 'Experience' section of a LinkedIn profile."""

    model_config = ConfigDict(populate_by_name=True)

    company_name: Optional[str] = Field(None, description="Name of the employer.")
    company_linkedin_url: Optional[str] = Field(None, description="LinkedIn URL for the company page.")
    company_logo_url: Optional[str] = Field(None, description="Company logo image URL.")
    title: Optional[str] = Field(None, description="Job title / role.")
    employment_type: Optional[str] = Field(None, description="E.g. Full-time, Contract, Internship.")
    location: Optional[str] = Field(None, description="Office / remote location string.")
    description: Optional[str] = Field(None, description="Role description / responsibilities.")
    date_range: Optional[DateRange] = Field(None, description="Start and end dates.")


class EducationItem(BaseModel):
    """A single entry in the 'Education' section of a LinkedIn profile."""

    model_config = ConfigDict(populate_by_name=True)

    school_name: Optional[str] = Field(None, description="Name of the institution.")
    school_linkedin_url: Optional[str] = Field(None, description="LinkedIn URL for the school page.")
    school_logo_url: Optional[str] = Field(None, description="School logo image URL.")
    degree_name: Optional[str] = Field(None, description="Degree, e.g. Bachelor of Science.")
    field_of_study: Optional[str] = Field(None, description="Major / field, e.g. Computer Science.")
    grade: Optional[str] = Field(None, description="GPA or grade if shared.")
    description: Optional[str] = Field(None, description="Additional details.")
    date_range: Optional[DateRange] = Field(None, description="Start and end years.")


class SkillItem(BaseModel):
    """A single skill entry."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Skill name, e.g. 'Python'.")
    endorsement_count: Optional[int] = Field(None, description="Number of endorsements.")


class CertificationItem(BaseModel):
    """A single certification / licence."""

    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, description="Certification name.")
    authority: Optional[str] = Field(None, description="Issuing organisation.")
    license_number: Optional[str] = Field(None, description="Licence or credential ID.")
    url: Optional[str] = Field(None, description="Verification URL.")
    date_range: Optional[DateRange] = Field(None, description="Issue / expiry dates.")


class LanguageItem(BaseModel):
    """A single language entry."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Language name, e.g. 'English'.")
    proficiency: Optional[str] = Field(
        None,
        description="LinkedIn proficiency key, e.g. NATIVE_OR_BILINGUAL, PROFESSIONAL_WORKING.",
    )


class ProfilePicture(BaseModel):
    """Profile photo metadata."""

    model_config = ConfigDict(populate_by_name=True)

    url: Optional[str] = Field(None, description="Direct URL to the profile image.")
    width: Optional[int] = Field(None, description="Image width in pixels.")
    height: Optional[int] = Field(None, description="Image height in pixels.")


# ── Top-level response model ────────────────────────────────────────────────


class ProfileResponse(BaseModel):
    """
    Complete structured response for a single LinkedIn profile.
    All fields are optional to handle partial / private profiles gracefully.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Identity
    public_identifier: Optional[str] = Field(None, description="LinkedIn username slug, e.g. 'john-doe-123'.")
    first_name: Optional[str] = Field(None, description="First name.")
    last_name: Optional[str] = Field(None, description="Last name.")
    full_name: Optional[str] = Field(None, description="Concatenated full name.")
    headline: Optional[str] = Field(None, description="Profile headline / tagline.")
    summary: Optional[str] = Field(None, description="About / summary section text.")

    # Location
    location_name: Optional[str] = Field(None, description="Displayed location string.")
    country_code: Optional[str] = Field(None, description="ISO country code, e.g. 'us'.")

    # Profile picture
    profile_picture: Optional[ProfilePicture] = Field(None, description="Profile photo details.")

    # Connections / followers
    connection_count: Optional[int] = Field(None, description="Number of connections.")
    follower_count: Optional[int] = Field(None, description="Number of followers.")

    # Sections
    experience: list[ExperienceItem] = Field(default_factory=list, description="Work experience entries.")
    education: list[EducationItem] = Field(default_factory=list, description="Education entries.")
    skills: list[SkillItem] = Field(default_factory=list, description="Listed skills.")
    certifications: list[CertificationItem] = Field(default_factory=list, description="Certifications / licences.")
    languages: list[LanguageItem] = Field(default_factory=list, description="Languages spoken.")

    # Meta
    linkedin_url: Optional[str] = Field(None, description="Canonical LinkedIn profile URL.")
    scraped_at: Optional[str] = Field(None, description="ISO-8601 timestamp of when data was fetched.")


# ── Request models ──────────────────────────────────────────────────────────


class ProfileRequest(BaseModel):
    """Body payload for `POST /api/v1/profile`."""

    model_config = ConfigDict(populate_by_name=True)

    url: str = Field(
        ...,
        description="LinkedIn profile URL, e.g. https://www.linkedin.com/in/username",
        examples=["https://www.linkedin.com/in/satyanadella"],
    )

    @field_validator("url")
    @classmethod
    def validate_linkedin_url(cls, v: str) -> str:
        cleaned = v.strip()
        if "linkedin.com/in/" not in cleaned:
            raise ValueError(
                "URL must be a LinkedIn profile URL containing '/in/', "
                f"e.g. https://www.linkedin.com/in/username. Got: {cleaned!r}"
            )
        return cleaned


# ── Health-check response ───────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Response model for the GET / health endpoint."""

    status: str = Field("ok", description="Service status.")
    service: str = Field("linkedin-voyager-api", description="Service name.")
    version: str = Field("1.0.0", description="API version.")
    environment: Optional[str] = Field(None, description="Runtime environment.")


# ── Error response ──────────────────────────────────────────────────────────


class ErrorDetail(BaseModel):
    """Standardised error body returned for all 4xx/5xx responses."""

    error: str = Field(..., description="Short error code / type.")
    message: str = Field(..., description="Human-readable explanation.")
    detail: Optional[str] = Field(None, description="Optional technical detail for debugging.")
