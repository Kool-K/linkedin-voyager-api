import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.utils.helpers import extract_linkedin_slug
from app.parsers.profile_parser import parse_profile

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_health_check(client):
    """Verifies GET / returns 200 OK and status environment."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "environment" in data
    assert data["environment"] in ["development", "production", "test"]

def test_url_normalizer():
    """Tests extract_linkedin_slug with diverse URL formats."""
    # Standard URLs
    assert extract_linkedin_slug("https://www.linkedin.com/in/satyanadella/") == "satyanadella"
    # Subdomains and query tracking
    assert extract_linkedin_slug("https://in.linkedin.com/in/john-doe?trk=nav") == "john-doe"
    assert extract_linkedin_slug("https://uk.linkedin.com/in/username?miniProfileUrn=urn%3Ali%3Afs_miniProfile") == "username"
    # Pure slugs
    assert extract_linkedin_slug("satyanadella") == "satyanadella"
    assert extract_linkedin_slug("in/satyanadella") == "satyanadella"
    # Invalid URLs
    assert extract_linkedin_slug("https://google.com") is None
    assert extract_linkedin_slug("invalid/url") is None

def test_invalid_url_rejection(client):
    """Sends POST /api/v1/profile with an invalid URL and verifies HTTP 422 response (schema validation)."""
    response = client.post("/api/v1/profile", json={"url": "https://google.com"})
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert "LinkedIn" in data["detail"][0]["msg"]

def test_invalid_url_rejection_get(client):
    """Sends GET /api/v1/profile with an invalid URL and verifies HTTP 400 response (route validation)."""
    response = client.get("/api/v1/profile?url=https://google.com")
    assert response.status_code == 400
    data = response.json()
    assert data.get("error") == "bad_request"
    assert "Invalid LinkedIn profile URL" in data.get("message", "")

def test_profile_parser_mock():
    """Passes a mock normalized JSON payload to parse_profile() and asserts extractions."""
    mock_payload = {
        "included": [
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
                "publicIdentifier": "satyanadella",
                "firstName": "Satya",
                "lastName": "Nadella",
                "headline": "Chairman and CEO at Microsoft",
                "summary": "Building things.",
                "locationName": "Redmond, Washington",
                "geoCountryName": "us"
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Position",
                "title": "CEO",
                "companyName": "Microsoft",
                "locationName": "Redmond",
                "dateRange": {
                    "start": {"year": 2014, "month": 2}
                }
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Education",
                "schoolName": "University of Chicago",
                "degreeName": "MBA"
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Skill",
                "name": "Cloud Computing",
                "endorsementCount": 99
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.profile.Language",
                "name": "English",
                "proficiency": "NATIVE_OR_BILINGUAL"
            }
        ]
    }
    
    profile = parse_profile(mock_payload, "satyanadella")
    
    # Assert Basics
    assert profile.first_name == "Satya"
    assert profile.last_name == "Nadella"
    assert profile.headline == "Chairman and CEO at Microsoft"
    assert profile.summary == "Building things."
    
    # Assert Experience
    assert len(profile.experience) == 1
    assert profile.experience[0].title == "CEO"
    assert profile.experience[0].company_name == "Microsoft"
    assert profile.experience[0].date_range.is_current is True
    
    # Assert Education
    assert len(profile.education) == 1
    assert profile.education[0].school_name == "University of Chicago"
    
    # Assert Skills
    assert len(profile.skills) == 1
    assert profile.skills[0].name == "Cloud Computing"
    assert profile.skills[0].endorsement_count == 99
    
    # Assert Languages
    assert len(profile.languages) == 1
    assert profile.languages[0].name == "English"
    assert profile.languages[0].proficiency == "NATIVE_OR_BILINGUAL"

def test_get_profile_endpoint_docs(client):
    """Verifies route structure and OpenAPI schema registration."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    
    openapi = response.json()
    paths = openapi.get("paths", {})
    
    assert "/" in paths
    assert "/api/v1/profile" in paths
    
    # Verify both GET and POST are registered on the profile endpoint
    assert "post" in paths["/api/v1/profile"]
    assert "get" in paths["/api/v1/profile"]
