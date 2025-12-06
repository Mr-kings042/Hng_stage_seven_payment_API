from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from model import Transaction, User
from utils import create_access_token, get_current_user
from schemas import (
	GoogleAuthURLResponse,
	GoogleUserResponse,
	PaymentInitiateRequest,
	PaymentInitiateResponse,
	PaymentStatusResponse,
	WebhookAcknowledgement,
)
from services.auth import build_google_auth_url, exchange_code_for_user
from services.paystack import handle_webhook, initialize_transaction, verify_transaction


router = APIRouter(tags=["Payments API"])


@router.get("/auth/google", response_model=GoogleAuthURLResponse)
def trigger_google_sign_in(
	json: bool = Query(False, description="Return JSON instead of redirect")
):
	url = build_google_auth_url()
	if json:
		return {"google_auth_url": url}
	return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/google/callback", response_model=GoogleUserResponse)
def google_callback(
	response: Response,
	code: Optional[str] = Query(None),
	set_cookie: bool = Query(True, description="Also set JWT as httpOnly cookie"),
	db: Session = Depends(get_db),
):
	if not code:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Missing 'code' query parameter",
		)
	user = exchange_code_for_user(db, code)
	access_token = create_access_token(user)
	if set_cookie:
		response.set_cookie(
			key="access_token",
			value=access_token,
			httponly=True,
			secure=False,
			samesite="lax",
		)
	return {
		"user_id": user.id,
		"email": user.email,
		"name": user.name,
		"picture": user.picture,
		"access_token": access_token,
	}


@router.post(
	"/payments/paystack/initiate",
	response_model=PaymentInitiateResponse,
	status_code=status.HTTP_201_CREATED,
)
def initiate_paystack_payment(
	payload: PaymentInitiateRequest,
	current_user: User = Depends(get_current_user),
	db: Session = Depends(get_db),
):
	txn = initialize_transaction(db, user_id=current_user.id, amount=payload.amount)
	return {
		"reference": txn.reference,
		"authorization_url": txn.authorization_url,
	}


@router.post("/payments/paystack/webhook", response_model=WebhookAcknowledgement)
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
	raw_body = await request.body()
	signature = request.headers.get("x-paystack-signature")
	handle_webhook(db, raw_body, signature)
	return {"status": True}


@router.get("/payments/{reference}/status", response_model=PaymentStatusResponse)
def get_payment_status(
	reference: str,
	refresh: bool = Query(False, description="Force refresh from Paystack"),
	current_user: User = Depends(get_current_user),
	db: Session = Depends(get_db),
):
	transaction = db.query(Transaction).filter(Transaction.reference == reference).first()
	if transaction and transaction.user_id != current_user.id:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Not authorized to view this transaction",
		)
	if not transaction and not refresh:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Transaction not found",
		)
	if refresh or not transaction:
		transaction = verify_transaction(db, reference)
	return {
		"reference": transaction.reference,
		"status": getattr(transaction.status, "value", str(transaction.status)),
		"amount": int(transaction.amount or 0),
		"paid_at": transaction.paid_at.isoformat() if transaction.paid_at else None,
	}
