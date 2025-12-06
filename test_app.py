import json
import os
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from database import get_db
from model import Transaction, TransactionStatus, User


client = TestClient(app)


@pytest.fixture(autouse=True)
def _set_jwt_env(monkeypatch):
	monkeypatch.setenv("JWT_SECRET", "test-secret")
	monkeypatch.setenv("JWT_ALGORITHM", "HS256")
	yield


@pytest.fixture
def mock_db_session():
	session = MagicMock()
	app.dependency_overrides[get_db] = lambda: session
	yield session
	app.dependency_overrides.clear()


def test_google_auth_redirect(monkeypatch):
	monkeypatch.setattr("routes.build_google_auth_url", lambda: "https://accounts.google.com/mock")
	resp = client.get("/auth/google", follow_redirects=False)
	assert resp.status_code == 302
	assert "accounts.google.com" in resp.headers.get("location", "")


def test_google_auth_json_url(monkeypatch):
	monkeypatch.setattr("routes.build_google_auth_url", lambda: "https://accounts.google.com/mock")
	resp = client.get("/auth/google", params={"json": True})
	assert resp.status_code == 200
	body = resp.json()
	assert "google_auth_url" in body
	assert body["google_auth_url"].startswith("https://accounts.google.com/")


def test_google_callback_missing_code(mock_db_session):
	resp = client.get("/auth/google/callback")
	assert resp.status_code == 400


def test_google_callback_invalid_code(monkeypatch):
	monkeypatch.setattr(
		"routes.exchange_code_for_user",
		lambda db, code: (_ for _ in ()).throw(
			HTTPException(status_code=401, detail="Invalid code")
		),
	)
	resp = client.get("/auth/google/callback", params={"code": "bad"})
	assert resp.status_code == 401


def test_google_callback_sets_token_and_cookie(mock_db_session, monkeypatch):
	fake_user = User(id="u1", email="u@example.com", name="U", picture=None)
	monkeypatch.setattr("routes.exchange_code_for_user", lambda db, code: fake_user)
	monkeypatch.setattr("routes.create_access_token", lambda user: "tok123")

	resp = client.get("/auth/google/callback", params={"code": "abc", "set_cookie": True})

	assert resp.status_code == 200
	data = resp.json()
	assert data["access_token"] == "tok123"
	assert resp.cookies.get("access_token") == "tok123"


def test_payment_initiate_requires_auth(mock_db_session):
	resp = client.post("/payments/paystack/initiate", json={"amount": 5000})
	assert resp.status_code == 401


def test_payment_initiate_bad_amount(mock_db_session, monkeypatch):
	fake_user = User(id="u1", email="u@example.com", name="U", picture=None)
	app.dependency_overrides[__import__("utils").get_current_user] = lambda: fake_user
	resp = client.post(
		"/payments/paystack/initiate",
		json={"amount": 0},
		headers={"Authorization": "Bearer token"},
	)
	# Pydantic validation fails first -> 422 Unprocessable Entity
	assert resp.status_code == 422


def test_payment_initiate_duplicate_returns_existing(mock_db_session, monkeypatch):
	fake_user = User(id="u1", email="u@example.com", name="U", picture=None)
	app.dependency_overrides[__import__("utils").get_current_user] = lambda: fake_user
	existing = Transaction(
		reference="ref_existing",
		user_id=fake_user.id,
		amount=5000,
		currency="NGN",
		status=TransactionStatus.PENDING,
		authorization_url="https://paystack/existing",
	)
	monkeypatch.setattr(
		"routes.initialize_transaction",
		lambda db, user_id, amount: existing,
	)
	resp = client.post(
		"/payments/paystack/initiate",
		json={"amount": 5000},
		headers={"Authorization": "Bearer token"},
	)
	assert resp.status_code == 201
	data = resp.json()
	assert data["reference"] == "ref_existing"
	assert data["authorization_url"].endswith("/existing")


def test_payment_initiate_success(mock_db_session, monkeypatch):
	fake_user = User(id="u1", email="u@example.com", name="U", picture=None)
	app.dependency_overrides[get_db] = lambda: mock_db_session
	app.dependency_overrides[__import__("utils").get_current_user] = lambda: fake_user

	txn = Transaction(
		reference="ref123",
		user_id=fake_user.id,
		amount=5000,
		currency="NGN",
		status=TransactionStatus.PENDING,
		authorization_url="https://paystack/checkout",
	)
	monkeypatch.setattr("routes.initialize_transaction", lambda db, user_id, amount: txn)

	resp = client.post(
		"/payments/paystack/initiate",
		json={"amount": 5000},
		headers={"Authorization": "Bearer dummy"},
	)

	assert resp.status_code == 201
	data = resp.json()
	assert data["reference"] == "ref123"
	assert data["authorization_url"].startswith("https://")


def test_payment_status_refresh(mock_db_session, monkeypatch):
	fake_user = User(id="u1", email="u@example.com", name="U", picture=None)
	app.dependency_overrides[__import__("utils").get_current_user] = lambda: fake_user
	mock_db_session.query.return_value.filter.return_value.first.return_value = None
	txn = Transaction(
		reference="ref123",
		user_id=fake_user.id,
		amount=5000,
		currency="NGN",
		status=TransactionStatus.SUCCESS,
	)
	monkeypatch.setattr("routes.verify_transaction", lambda db, reference: txn)

	resp = client.get(
		"/payments/ref123/status",
		params={"refresh": True},
		headers={"Authorization": "Bearer token"},
	)

	assert resp.status_code == 200
	data = resp.json()
	assert data["reference"] == "ref123"
	assert data["status"] == "success"


def test_payment_status_forbidden_other_user(mock_db_session, monkeypatch):
	user_a = User(id="u1", email="u@example.com", name="U", picture=None)
	user_b = User(id="u2", email="b@example.com", name="B", picture=None)
	app.dependency_overrides[__import__("utils").get_current_user] = lambda: user_a
	txn = Transaction(
		reference="ref123",
		user_id=user_b.id,
		amount=5000,
		currency="NGN",
		status=TransactionStatus.SUCCESS,
	)
	mock_db_session.query.return_value.filter.return_value.first.return_value = txn
	resp = client.get(
		"/payments/ref123/status",
		headers={"Authorization": "Bearer token"},
	)
	assert resp.status_code == 403


def test_webhook_valid_signature(mock_db_session, monkeypatch):
	app.dependency_overrides[get_db] = lambda: mock_db_session
	monkeypatch.setattr("routes.handle_webhook", lambda db, raw, sig: Transaction(reference="r", user_id="u", amount=1, currency="NGN", status=TransactionStatus.SUCCESS))

	payload = {"data": {"reference": "r"}}
	resp = client.post(
		"/payments/paystack/webhook",
		data=json.dumps(payload),
		headers={"x-paystack-signature": "sig"},
	)

	assert resp.status_code == 200
	assert resp.json()["status"] is True

