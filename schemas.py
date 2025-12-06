from typing import Optional

from pydantic import AnyHttpUrl, BaseModel, EmailStr, Field


class GoogleAuthURLResponse(BaseModel):
	google_auth_url: AnyHttpUrl


class GoogleUserResponse(BaseModel):
	user_id: str
	email: EmailStr
	name: str
	picture: Optional[AnyHttpUrl] = None
	access_token: str


class PaymentInitiateRequest(BaseModel):
	amount: int = Field(..., gt=0, description="Amount in kobo")


class PaymentInitiateResponse(BaseModel):
	reference: str
	authorization_url: AnyHttpUrl


class PaymentStatusResponse(BaseModel):
	reference: str
	status: str
	amount: int
	paid_at: Optional[str] = None


class WebhookAcknowledgement(BaseModel):
	status: bool = True
