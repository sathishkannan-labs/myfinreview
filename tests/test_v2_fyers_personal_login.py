"""Controlled individual FYERS login: strict owner scope, safe callback and no secret output."""
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
from models.portable_models import User
from intelligence.routes import (
    fyers_test_availability,start_broker_authorization,
    broker_authorization_callback)
from intelligence.credential_vault import status,read_trusted

@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("FINREVIEW_ENV","staging")
    monkeypatch.setenv("FINREVIEW_V2_OAUTH_ENABLED","true")
    monkeypatch.setenv("FINREVIEW_FYERS_TEST_USER_ID","1")
    monkeypatch.setenv("FINREVIEW_BROKER_VAULT_KEY",Fernet.generate_key().decode())
    monkeypatch.setenv("FYERS_CLIENT_ID","fake-app-100")
    monkeypatch.setenv("FYERS_APP_SECRET","fake-test-secret")
    monkeypatch.setenv("FINREVIEW_FYERS_REDIRECT_URI","https://example.org/v2/market/oauth/fyers/callback")
    engine=create_engine("sqlite://",poolclass=StaticPool,connect_args={"check_same_thread":False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(email="fyers-test1@example.org",password="test"))
        session.add(User(email="fyers-test2@example.org",password="test"))
        session.commit()
        yield session

def test_allowlist_fails_closed(db,monkeypatch):
    assert fyers_test_availability(user=SimpleNamespace(id=1))["available"]
    assert not fyers_test_availability(user=SimpleNamespace(id=2))["available"]
    monkeypatch.delenv("FINREVIEW_FYERS_TEST_USER_ID")
    assert not fyers_test_availability(user=SimpleNamespace(id=1))["available"]
    monkeypatch.setenv("FINREVIEW_FYERS_TEST_USER_ID","not-an-id")
    assert not fyers_test_availability(user=SimpleNamespace(id=1))["available"]
    monkeypatch.setenv("FINREVIEW_ENV","production")
    assert not fyers_test_availability(user=SimpleNamespace(id=1))["available"]

@pytest.mark.asyncio
async def test_callback_never_reflects_secrets_and_stores_only_for_owner(db,monkeypatch):
    import intelligence.broker_oauth as oauth
    async def mocked_exchange(broker,code):
        assert broker=="fyers" and code=="sensitive-code"
        return {"access_token":"secret-broker-token","app_id":"fake-app-100"}
    monkeypatch.setattr(oauth,"exchange_code",mocked_exchange)
    info=start_broker_authorization("fyers",user=SimpleNamespace(id=1),session=db)
    state=parse_qs(urlparse(info["authorization_url"]).query)["state"][0]
    response=await broker_authorization_callback("fyers",
        request=SimpleNamespace(query_params={"auth_code":"sensitive-code","state":state}),
        session=db)
    assert response.status_code==303
    location=response.headers["location"]
    assert location=="https://finreview-v201-staging-ui.onrender.com/intelligence.html#fyersConnectionPanel"
    assert "sensitive-code" not in location and state not in location
    assert "secret-broker-token" not in location
    assert "sensitive-code" not in response.body.decode()
    assert response.headers["cache-control"]=="no-store"
    assert response.headers["referrer-policy"]=="no-referrer"
    assert len(status(db,1)["connections"])==2
    assert status(db,2)["connections"]==[]
    assert read_trusted(db,1,"fyers","quotes")["access_token"]=="secret-broker-token"
    repeat=await broker_authorization_callback("fyers",
        request=SimpleNamespace(query_params={"auth_code":"sensitive-code","state":state}),
        session=db)
    assert "unsuccessful or expired" in repeat.body.decode()
    assert "secret-broker-token" not in repeat.body.decode()

def test_container_disables_uvicorn_url_access_log():
    docker=(Path(__file__).resolve().parents[1]/"Dockerfile").read_text()
    assert "--no-access-log" in docker
