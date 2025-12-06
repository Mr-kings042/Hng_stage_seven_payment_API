import os
import urllib.parse
from typing import Optional

import requests
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from logger import get_logger
from model import User


logger = get_logger(__name__)


GOOGLE_AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _get_google_config() -> dict:
	client_id = os.getenv("GOOGLE_CLIENT_ID")
	client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
	redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
	if not all([client_id, client_secret, redirect_uri]):
		logger.error("Google OAuth environment variables are not fully configured")
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Google OAuth not configured",
		)
	return {
		"client_id": client_id,
		"client_secret": client_secret,
		"redirect_uri": redirect_uri,
	}


def build_google_auth_url(state: Optional[str] = None) -> str:
	cfg = _get_google_config()
	params = {
		"client_id": cfg["client_id"],
		"redirect_uri": cfg["redirect_uri"],
		"response_type": "code",
		"scope": "openid email profile",
		"access_type": "offline",
		"prompt": "consent",
	}
	if state:
		params["state"] = state
	query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
	return f"{GOOGLE_AUTH_BASE}?{query}"


def _upsert_user(db: Session, profile: dict) -> User:
	google_id = profile.get("sub")
	email = profile.get("email")
	if not email:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Google did not return an email",
		)
	user = (
		db.query(User)
		.filter((User.google_id == google_id) | (User.email == email))
		.first()
	)
	if user:
		user.name = profile.get("name") or user.name
		user.picture = profile.get("picture") or user.picture
		user.google_id = google_id or user.google_id
	else:
		user = User(
			email=email,
			name=profile.get("name") or "",
			picture=profile.get("picture"),
			google_id=google_id,
		)
		db.add(user)
	db.commit()
	db.refresh(user)
	return user


def exchange_code_for_user(db: Session, code: str) -> User:
	cfg = _get_google_config()
	data = {
		"code": code,
		"client_id": cfg["client_id"],
		"client_secret": cfg["client_secret"],
		"redirect_uri": cfg["redirect_uri"],
		"grant_type": "authorization_code",
	}
	token_resp = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=10)
	if token_resp.status_code != 200:
		logger.error("Google token exchange failed: %s", token_resp.text)
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid authorization code",
		)
	token_json = token_resp.json()
	access_token = token_json.get("access_token")
	if not access_token:
		logger.error("Google token response missing access_token: %s", token_json)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Google token missing access token",
		)
	userinfo_resp = requests.get(
		GOOGLE_USERINFO_URL,
		headers={"Authorization": f"Bearer {access_token}"},
		timeout=10,
	)
	if userinfo_resp.status_code != 200:
		logger.error("Google userinfo failed: %s", userinfo_resp.text)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Failed to fetch Google user info",
		)
	profile = userinfo_resp.json()
	return _upsert_user(db, profile)
