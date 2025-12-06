import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db
from model import User


JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
JWT_COOKIE_NAME = os.getenv("JWT_COOKIE_NAME", "access_token")


def create_access_token(user: User) -> str:
	if not JWT_SECRET:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="JWT secret not configured",
		)
	now = datetime.now(timezone.utc)
	payload = {
		"sub": user.id,
		"email": user.email,
		"name": user.name,
		"exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
		"iat": now,
	}
	return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
	request: Request,
	authorization: str = Header(None, description="Authorization: Bearer <token>"),
	db: Session = Depends(get_db),
) -> User:
	# Prefer Authorization header; fallback to httpOnly cookie
	if authorization and authorization.lower().startswith("bearer "):
		token = authorization.split(" ", 1)[1].strip()
	else:
		token = request.cookies.get(JWT_COOKIE_NAME)
	if not token:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Authorization header missing or invalid",
		)
	if not JWT_SECRET:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="JWT secret not configured",
		)
	try:
		payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
		user_id = payload.get("sub")
	except jwt.ExpiredSignatureError:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Token expired",
		)
	except jwt.InvalidTokenError:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid token",
		)
	if not user_id:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid token payload",
		)
	user = db.query(User).filter(User.id == user_id).first()
	if not user:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid user",
		)
	return user