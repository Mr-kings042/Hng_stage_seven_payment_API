import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime
from typing import Optional

import requests
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from logger import get_logger
from model import Transaction, TransactionStatus, User


logger = get_logger(__name__)


PAYSTACK_INITIALIZE_URL = "https://api.paystack.co/transaction/initialize"
PAYSTACK_VERIFY_URL = "https://api.paystack.co/transaction/verify"


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
	if not value:
		return None
	try:
		return datetime.fromisoformat(value.replace("Z", "+00:00"))
	except ValueError:
		return None


def _get_paystack_secret() -> str:
	secret = os.getenv("PAYSTACK_SECRET_KEY")
	if not secret:
		logger.error("PAYSTACK_SECRET_KEY is not configured")
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Paystack secret key not configured",
		)
	return secret


def _get_webhook_secret() -> Optional[str]:
	return os.getenv("PAYSTACK_WEBHOOK_SECRET")


def initialize_transaction(db: Session, user_id: str, amount: int) -> Transaction:
	if amount <= 0:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Amount must be greater than zero",
		)
	user = db.query(User).filter(User.id == user_id).first()
	if not user:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="User not found",
		)

	# Idempotency: return existing pending transaction for same user and amount
	existing = (
		db.query(Transaction)
		.filter(
			Transaction.user_id == user_id,
			Transaction.amount == amount,
			Transaction.status == TransactionStatus.PENDING,
		)
		.first()
	)
	if existing:
		return existing

	secret = _get_paystack_secret()
	reference = f"ps_{uuid.uuid4().hex}"
	payload = {
		"email": user.email,
		"amount": amount,
		"reference": reference,
		"currency": "NGN",
		"metadata": {"user_id": user_id},
	}
	headers = {"Authorization": f"Bearer {secret}"}
	resp = requests.post(PAYSTACK_INITIALIZE_URL, json=payload, headers=headers, timeout=10)
	if resp.status_code >= 400:
		logger.error("Paystack init HTTP error %s: %s", resp.status_code, resp.text)
		raise HTTPException(
			status_code=status.HTTP_402_PAYMENT_REQUIRED,
			detail="Paystack initialization failed",
		)
	body = resp.json()
	if not body.get("status"):
		logger.error("Paystack init failed response: %s", body)
		raise HTTPException(
			status_code=status.HTTP_402_PAYMENT_REQUIRED,
			detail=body.get("message") or "Payment initiation failed",
		)

	data = body.get("data") or {}
	txn = Transaction(
		reference=reference,
		user_id=user_id,
		amount=amount,
		currency=data.get("currency") or "NGN",
		status=TransactionStatus.PENDING,
		authorization_url=data.get("authorization_url"),
		access_code=data.get("access_code"),
		paystack_data=json.dumps(body),
	)
	db.add(txn)
	db.commit()
	db.refresh(txn)
	return txn


def _map_paystack_status(status_text: str) -> TransactionStatus:
	if status_text == "success":
		return TransactionStatus.SUCCESS
	if status_text == "failed":
		return TransactionStatus.FAILED
	if status_text == "abandoned":
		return TransactionStatus.ABANDONED
	return TransactionStatus.PENDING


def verify_transaction(db: Session, reference: str) -> Transaction:
	secret = _get_paystack_secret()
	headers = {"Authorization": f"Bearer {secret}"}
	resp = requests.get(f"{PAYSTACK_VERIFY_URL}/{reference}", headers=headers, timeout=10)
	if resp.status_code >= 400:
		logger.error("Paystack verify HTTP error %s: %s", resp.status_code, resp.text)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Failed to verify transaction",
		)
	body = resp.json()
	if not body.get("status"):
		logger.error("Paystack verify failed response: %s", body)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail=body.get("message") or "Verification failed",
		)
	data = body.get("data") or {}
	txn = db.query(Transaction).filter(Transaction.reference == reference).first()
	if not txn:
		user_id = data.get("metadata", {}).get("user_id")
		if not user_id:
			raise HTTPException(
				status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
				detail="Verification payload missing user_id",
			)
		txn = Transaction(
			reference=reference,
			user_id=user_id,
			amount=data.get("amount") or 0,
			currency=data.get("currency") or "NGN",
		)
		db.add(txn)

	txn.status = _map_paystack_status(data.get("status"))
	txn.authorization_url = data.get("authorization_url") or txn.authorization_url
	txn.paystack_data = json.dumps(body)
	paid_at = data.get("paid_at")
	txn.paid_at = _parse_datetime(paid_at)
	db.commit()
	db.refresh(txn)
	return txn


def handle_webhook(db: Session, raw_body: bytes, signature: Optional[str]) -> Transaction:
	secret = _get_webhook_secret()
	if not secret:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Webhook secret not configured",
		)
	if not signature:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Missing Paystack signature",
		)

	computed = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
	if not hmac.compare_digest(computed, signature):
		logger.warning("Invalid Paystack webhook signature")
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Invalid signature",
		)

	event = json.loads(raw_body.decode())
	data = event.get("data") or {}
	reference = data.get("reference")
	if not reference:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Webhook missing reference",
		)
	txn = db.query(Transaction).filter(Transaction.reference == reference).first()
	if not txn:
		user_id = data.get("metadata", {}).get("user_id")
		if not user_id:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="Webhook payload missing user_id",
			)
		txn = Transaction(
			reference=reference,
			user_id=user_id,
			amount=data.get("amount") or 0,
			currency=data.get("currency") or "NGN",
		)
		db.add(txn)

	txn.status = _map_paystack_status(data.get("status"))
	txn.paid_at = _parse_datetime(data.get("paid_at"))
	txn.paystack_data = json.dumps(event)
	db.commit()
	db.refresh(txn)
	return txn
