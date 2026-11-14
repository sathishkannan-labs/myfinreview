import sys
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from models.portable_models import User
from intelligence.routes import start_broker_authorization, broker_authorization_callback
from intelligence.credential_vault import status

@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("FINREVIEW_BROKER_VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("UPSTOX_CLIENT_ID", "test-app")
    monkeypatch.setenv("UPSTOX_CLIENT_SECRET", "secret")
    monkeypatch.setenv("FINREVIEW_UPSTOX_REDIRECT_URI",
                       "https://example.org/v2/market/oauth/upstox/callback")
    e=create_engine("sqlite://",poolclass=StaticPool,connect_args={"check_same_thread":False})
    SQLModel.metadata.create_all(e)
    with Session(e) as session:
        session.add(User(email="oauthroute@example.com",password="test"))
        session.commit()
        yield session

def test_start_is_off_by_default(db, monkeypatch):
    monkeypatch.delenv("FINREVIEW_V2_OAUTH_ENABLED", raising=False)
    with pytest.raises(HTTPException) as error:
        start_broker_authorization("upstox",user=SimpleNamespace(id=1),session=db)
    assert error.value.status_code == 403
    assert status(db,1)["connections"] == []

def test_enabled_start_return_only_opaque_state(db, monkeypatch):
    monkeypatch.setenv("FINREVIEW_V2_OAUTH_ENABLED","true")
    result=start_broker_authorization("upstox",user=SimpleNamespace(id=1),session=db)
    assert result["provider"]=="upstox"
    assert result["status"]=="PENDING"
    assert "secret" not in result["authorization_url"]
    assert "state=" in result["authorization_url"]

@pytest.mark.asyncio
async def test_callback_is_off_by_default(db, monkeypatch):
    monkeypatch.delenv("FINREVIEW_V2_OAUTH_ENABLED",raising=False)
    with pytest.raises(HTTPException) as error:
        await broker_authorization_callback(
            "upstox",request=SimpleNamespace(query_params={"state":"fake","code":"fake"}),
            session=db)
    assert error.value.status_code==403
