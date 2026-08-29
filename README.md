# 🔍 LinkedIn Voyager REST API Scraper

![Python 3.12](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141.1-009688?logo=fastapi)
![Uvicorn](https://img.shields.io/badge/Uvicorn-0.52.4-499848)
![HTTPX](https://img.shields.io/badge/HTTPX-0.28.1-blue)

A production-grade, browser-less LinkedIn profile scraper built on FastAPI. This repository reverse-engineers LinkedIn's internal **Voyager REST API** to extract rich, structured profile data without the overhead or detection risks of a headless browser.

Tailored for the **Tross Engineering Challenge**.

---

## 🏗️ Architecture & Engineering Approach

### Why Browser-less (API Reverse-Engineering)?
Traditional scraping approaches rely on headless browsers (Puppeteer, Playwright, Selenium). These have significant downsides for a high-scale production system:
1. **Performance Overhead**: Headless browsers consume significant CPU/Memory and take several seconds to load a page and execute JavaScript. This API scraper achieves **sub-100ms** response times.
2. **Fingerprint Detection**: LinkedIn actively blocks automated browsers using advanced fingerprinting. By replicating the exact API HTTP requests (with proper HTTP/2 multiplexing, cipher suites, and headers), we avoid client-side bot detection entirely.

### Dual-Strategy Voyager Resolution
The scraper implements a robust, fallback-driven extraction strategy:
- **Primary (Dash Endpoint)**: `/voyager/api/identity/dash/profiles` — Returns a deeply normalized graph JSON array (`included[]`). We parse this flat graph by identifying the `$type` discriminator.
- **Fallback (Classic Endpoint)**: `/voyager/api/identity/profiles/{username}/profileView` — Used if the Dash endpoint fails or isn't available. Returns a hierarchical nested structure (`positionView`, `educationView`, etc.).

### Session & CSRF Authentication
To authorize requests, LinkedIn's Voyager API demands a strict pairing of session cookies and CSRF headers:
- `li_at`: The core session authentication JWT-like cookie.
- `JSESSIONID`: The session tracking ID (e.g., `"ajax:123456..."`).
- `csrf-token`: A mandatory request header. This must exactly match the `JSESSIONID` value with the surrounding quotes stripped.

---

## 📡 API Specification

- **Base URL**: `http://localhost:8000`
- **Swagger UI**: [`/docs`](http://localhost:8000/docs) (Interactive API documentation)
- **ReDoc**: [`/redoc`](http://localhost:8000/redoc)

### Endpoints

#### `GET /` — Health Check
Validates that the service is running and configured correctly.

#### `POST /api/v1/profile`
Extracts a structured profile from a LinkedIn URL.

**Example Request:**
```bash
curl -X 'POST' \
  'http://localhost:8000/api/v1/profile' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "url": "https://www.linkedin.com/in/satyanadella"
}'
```

#### `GET /api/v1/profile`
Alternative GET endpoint for easy browser/curl testing.

**Example Request:**
```bash
curl "http://localhost:8000/api/v1/profile?url=https://www.linkedin.com/in/satyanadella"
```

**Example JSON Response Payload:**
```json
{
  "public_identifier": "satyanadella",
  "first_name": "Satya",
  "last_name": "Nadella",
  "full_name": "Satya Nadella",
  "headline": "Chairman and CEO at Microsoft",
  "summary": "...",
  "location_name": "Redmond, Washington",
  "country_code": "us",
  "profile_picture": {
    "url": "https://media.licdn.com/dms/image/...",
    "width": 400,
    "height": 400
  },
  "connection_count": 500,
  "follower_count": 9800000,
  "experience": [
    {
      "company_name": "Microsoft",
      "title": "Chairman and CEO",
      "employment_type": "Full-time",
      "location": "Redmond, WA",
      "date_range": {
        "start_year": 2014,
        "start_month": 2,
        "end_year": null,
        "end_month": null,
        "is_current": true
      }
    }
  ],
  "education": [...],
  "skills": [...],
  "certifications": [...],
  "languages": [...],
  "linkedin_url": "https://www.linkedin.com/in/satyanadella",
  "scraped_at": "2024-01-15T10:30:00+00:00"
}
```

---

## 🚀 Local Setup & Development Guide

### 1. Prerequisites
- Python 3.12+
- A valid LinkedIn account

### 2. Installation
```bash
# Clone the repository
git clone <your-repo-url>
cd Tross

# Create and activate a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy the example environment file:
```bash
cp .env.example .env
```

**Retrieving your LinkedIn Cookies:**
1. Open [linkedin.com](https://www.linkedin.com) in Chrome/Firefox.
2. Log in to your account.
3. Open Developer Tools (`F12` or `Cmd+Option+I`) → **Application** tab → **Cookies** → `https://www.linkedin.com`.
4. Copy the value of `li_at` and paste it into `.env` as `LINKEDIN_LI_AT`.
5. Copy the exact value of `JSESSIONID` (including quotes if present, e.g., `"ajax:58525345..."`) and paste it into `.env` as `LINKEDIN_JSESSIONID`.

### 4. Running the Server
```bash
uvicorn app.main:app --reload
```
The API is now running at `http://127.0.0.1:8000`.

---

## ☁️ Production Deployment (Render / Cloud)

This application is production-ready and designed to deploy easily to PaaS providers like Render, Heroku, or Railway via the included `Procfile`.

### Render Deployment Steps:
1. Connect your GitHub repository to Render as a **Web Service**.
2. **Build Command**: `pip install -r requirements.txt`
3. **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2` (or simply rely on the `Procfile`).
4. **Environment Variables**:
   Navigate to the Environment section and securely inject:
   - `LINKEDIN_LI_AT`: Your active `li_at` token.
   - `LINKEDIN_JSESSIONID`: Your active `JSESSIONID` token.
   - `APP_ENV`: `production`

---

## ⚠️ Known Limitations & Mitigation Strategies

### 1. Cookie Lifecycle and Expiration
**Limitation**: The `li_at` session cookie is inherently tied to a user's browser session. It will expire if the user logs out manually, or naturally after a few months (or sooner based on LinkedIn's dynamic security policies).
**Mitigation**: For a true enterprise deployment, implement an automated token rotation system. A background worker (using Playwright) can periodically log into burner accounts, solve any initial CAPTCHAs, extract fresh cookies, and update a central Redis credential store that the FastAPI service reads from.

### 2. Rate Limiting and IP Reputation
**Limitation**: LinkedIn aggressively monitors request velocity from single IPs or single accounts. Exceeding velocity limits yields `HTTP 429 Too Many Requests` or `HTTP 999` blocks.
**Mitigation**:
- **IP Proxies**: Route `httpx` traffic through a pool of high-quality rotating residential proxies.
- **Account Pooling**: Distribute requests across a fleet of LinkedIn accounts rather than tying all traffic to a single set of cookies.
- **Jitter & Delays**: Introduce randomized delays (jitter) between sequential requests.

### 3. Profile Privacy Scopes
**Limitation**: Depending on the extraction account's 1st/2nd/3rd-degree network distance from the target profile, some fields (like full names, specific job descriptions, or contact info) may be masked by LinkedIn's privacy controls.
**Mitigation**: Use an account with a highly connected "LION" (LinkedIn Open Networker) status or a premium Sales Navigator tier, which expands visibility into 3rd-degree profiles. Ensure your API degrades gracefully when optional fields are missing (handled by the Pydantic schemas).
