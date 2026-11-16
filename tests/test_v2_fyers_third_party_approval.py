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
from intelligence.credential_vault import store_trusted
from intelligence.routes import (
    broker_connection_status,
    disconnect_broker,
    fyers_test_availability,
    start_broker_authorization,
)
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


def test_disabled_mode_still_exposes_and_deletes_local_session(app_db, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from main import _issue_token

    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "true")
    user = SimpleNamespace(id=1)
    auth = "Bearer " + _issue_token(user.id)
    state = parse_qs(urlparse(fyers_session.begin(app_db, user.id, auth)["authorization_url"]).query)["state"][0]
    digest = fyers_session.claim(app_db, state)
    fyers_session.finish(
        app_db,
        digest,
        {"access_token": "private-fixture"},
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    session_row = app_db.get(fyers_session.FyersSession, fyers_session.session_key(auth))
    session_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    app_db.add(session_row)
    app_db.commit()
    store_trusted(
        app_db,
        user.id,
        "fyers",
        "historical",
        {"access_token": "fresh-personal-staging-fixture"},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "false")
    status = broker_connection_status(user=user, session=app_db, authorization=auth)
    assert len(status["connections"]) == 1
    assert status["connections"][0]["store"] == "credential_vault"
    assert status["connections"][0]["state"] == "STORED_UNVERIFIED"
    assert len(status["inactive_connections"]) == 2
    assert {item["store"] for item in status["inactive_connections"]} == {"third_party_session"}
    assert {item["state"] for item in status["inactive_connections"]} == {"EXPIRED"}
    assert "fresh-personal-staging-fixture" not in str(status)
    disconnect_broker("fyers", user=user, session=app_db, authorization=auth)
    status = broker_connection_status(user=user, session=app_db, authorization=auth)
    assert status["connections"] == []
    assert status["inactive_connections"] == []
