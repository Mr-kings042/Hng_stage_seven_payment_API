# Payments API (FastAPI)

Backend-only Google Sign-In and Paystack payments with FastAPI. Provides OAuth-based user auth, Paystack transaction initiation, webhook handling, idempotency, rate limiting, and JWT-protected payment endpoints.

## Features
- Google OAuth2 login (redirect-first, JSON URL optional)
- JWT issuance and httpOnly cookie support
- Auth-protected Paystack payment initiation
- Paystack webhook validation (HMAC SHA512)
- On-demand Paystack verify and DB status refresh
- Idempotent initiation (reuse pending txn per user/amount)
- Request ID logging and simple rate limiting middleware
- Pydantic validation and clear error responses
- Test suite with FastAPI TestClient and mocks

## Tech Stack
- FastAPI
- SQLAlchemy
- Pydantic
- Requests
- PyJWT
- Paystack REST API

## Requirements
- Python 3.10+
- Paystack Secret Key (`PAYSTACK_SECRET_KEY`)
- Paystack Webhook Secret (`PAYSTACK_WEBHOOK_SECRET`)
- Google OAuth credentials: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`
- Database URL (`DATABASE_URL`) e.g. `sqlite:///./test.db`
- JWT secret (`JWT_SECRET`)

## Environment Variables
```
DATABASE_URL=sqlite:///./test.db
GOOGLE_CLIENT_ID=<your_google_client_id>
GOOGLE_CLIENT_SECRET=<your_google_client_secret>
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/auth/google/callback
PAYSTACK_SECRET_KEY=<your_paystack_secret>
PAYSTACK_WEBHOOK_SECRET=<your_paystack_webhook_secret>
JWT_SECRET=please-change-me
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60
RATE_LIMIT_MAX_REQUESTS=100
RATE_LIMIT_WINDOW_SECONDS=60
```

## Installation
```bash
python -m venv venv
venv\Scripts\activate   # on Windows
pip install -r requirements.txt  # or pip install fastapi uvicorn sqlalchemy requests python-dotenv PyJWT
```

## Running
```bash
uvicorn main:app --reload --port 8000
```
Visit `http://127.0.0.1:8000/docs` for interactive Swagger.

## API Overview

**GET /auth/google**
- Default: 302 redirect to Google consent
- Optional: `/auth/google?json=true` returns `{ "google_auth_url": "..." }`

**GET /auth/google/callback**
- Exchanges `code` for tokens, fetches profile, upserts user, issues JWT
- Response: `{ user_id, email, name, picture, access_token }`
- Optional: `set_cookie=true` sets `access_token` as httpOnly cookie

**POST /payments/paystack/initiate**
- Auth required (JWT via `Authorization: Bearer <token>`)
- Body: `{ "amount": <int kobo> }`
- Creates or reuses pending transaction (idempotent per user+amount)
- Response: `{ reference, authorization_url }`

**GET /payments/{reference}/status**
- Auth required
- Returns DB status; `refresh=true` triggers Paystack verify

**POST /payments/paystack/webhook**
- Validates `x-paystack-signature` (HMAC SHA512)
- Updates transaction status; responds `{ "status": true }`

## Auth & Security
- JWT bearer (`Authorization: Bearer <token>`) or `access_token` cookie
- Do not expose secrets; keep JWT secret strong
- Verify webhook signatures; use HTTPS in production; set cookie `secure=True` in prod

## Idempotency
- Payment initiation reuses existing pending transaction for the same user and amount, returning the existing `reference` and `authorization_url`.

## Testing
```bash
pytest
```
Tests cover Google auth flows, JWT cookie issuance, payment initiation (auth/idempotency/validation), status checks, and webhook handling (mocked).

## Rate Limiting
- Simple in-memory limiter; configure via `RATE_LIMIT_MAX_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS`.

## Logging
- Request ID middleware adds `X-Request-ID` and `X-Process-Time` headers; logs to console/file via `logger.py`.

## Notes
- For SQLite async URLs, code normalizes to sync driver. For production, use Postgres or MySQL with proper `DATABASE_URL`.
- Replace placeholder secrets before deployment.
