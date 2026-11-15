"""Fail-closed third-party FYERS approval gate and session isolation tests."""
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from intelligence import fyers_session
from intelligence.broker_oauth import BrokerAuthError
from intelligence.routes import fyers_test_availability, start_broker_authorization
from models.portable_models import User


@pytest.fixture
def app_db(monkeypatch):
    monkeypatch.setenv("FINREVIEW_ENV", "production")
    monkeypatch.setenv("FINREVIEW_V2_OAUTH_ENABLED", "false")
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_ENABLED", "true")
    monkeypatch.delenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", raising=False)
    monkeypatch.setenv("FINREVIEW_BROKER_VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FYERS_CLIENT_ID", "fixture-approved-app-100")
    monkeypatch.setenv("FYERS_APP_SECRET", "fixture-secret")
    monkeypatch.setenv("FINREVIEW_FYERS_REDIRECT_URI", "https://example.org/v2/market/oauth/fyers/callback")
    monkeypatch.setenv("FINREVIEW_FYERS_FRONTEND_RETURN_URL", "https://example.org/intelligence.html")
    monkeypatch.setenv("AUTH_SECRET_KEY", "fixture-third-party-secret-at-least-32-bytes")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(email="third-party@example.org", password="fixture"))
        db.commit()
        yield db


def test_approval_gate_is_off_even_with_activation_requested(app_db, monkeypatch):
    from fastapi import HTTPException
    from main import _issue_token

    user = SimpleNamespace(id=1)
    bearer = "Bearer " + _issue_token(user.id)
    assert not fyers_session.enabled()
    assert fyers_test_availability(user=user)["available"] is False
    with pytest.raises(HTTPException) as exc:
        start_broker_authorization("fyers", user=user, session=app_db, authorization=bearer)
    assert exc.value.status_code == 403
    assert fyers_session.status(app_db, user.id, bearer)["connections"] == []
    with pytest.raises(BrokerAuthError):
        fyers_session.read(app_db, user.id, bearer)
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "false")
    assert not fyers_session.enabled()
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "TRUE")
    assert fyers_session.enabled()
    assert fyers_test_availability(user=user)["mode"] == "THIRD_PARTY_SESSION"
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_ENABLED", "false")
    assert not fyers_session.enabled()


def test_state_claim_fails_if_activation_revoked(app_db, monkeypatch):
    from main import _issue_token

    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "true")
    auth = "Bearer " + _issue_token(1)
    url = fyers_session.begin(app_db, 1, auth)["authorization_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "false")
    with pytest.raises(BrokerAuthError):
        fyers_session.claim(app_db, state)


def test_browser_only_receives_app_login_url_not_secret(app_db, monkeypatch):
    from main import _issue_token

    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "true")
    bearer = "Bearer " + _issue_token(1)
    result = fyers_session.begin(app_db, 1, bearer)
    assert result["status"] == "PENDING"
    assert "fixture-secret" not in str(result)
    assert "access_token" not in str(result)
    assert result["authorization_url"].startswith("https://api-t1.fyers.in/")
