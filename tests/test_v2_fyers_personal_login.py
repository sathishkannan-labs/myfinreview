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
    broker_authorization_callback, fyers_test_market_data)
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

@pytest.mark.asyncio
async def test_market_probe_is_off_for_wrong_user_and_no_explicit_opt_in(db,monkeypatch):
    from fastapi import HTTPException
    monkeypatch.delenv("FINREVIEW_V2_BROKER_PROBES_ENABLED",raising=False)
    with pytest.raises(HTTPException) as exc:
        await fyers_test_market_data(user=SimpleNamespace(id=1),session=db)
    assert exc.value.status_code==403
    monkeypatch.setenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","true")
    with pytest.raises(HTTPException) as exc:
        await fyers_test_market_data(user=SimpleNamespace(id=2),session=db)
    assert exc.value.status_code==403


@pytest.mark.asyncio
async def test_market_probe_is_account_scoped_and_never_discloses_prices(db,monkeypatch):
    import intelligence.market_smoke as smoke
    from intelligence.market_smoke import MarketSmokeResult
    monkeypatch.setenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","true")
    seen={}
    async def fake_check(session,user_id,broker,**kwargs):
        seen.update(user=user_id,broker=broker,kwargs=kwargs)
        return MarketSmokeResult("fyers","AUTHENTICATED","VERIFIED_SAMPLE",
                                 "VERIFIED_SAMPLE",4,"private-fingerprint")
    monkeypatch.setattr(smoke,"check_stored_market_access",fake_check)
    result=await fyers_test_market_data(user=SimpleNamespace(id=1),session=db)
    assert seen["user"]==1 and seen["broker"]=="fyers"
    assert seen["kwargs"]["history_instrument"]=="NSE:INFY-EQ"
    assert seen["kwargs"]["quote_instrument"]=="NSE:INFY-EQ"
    assert (seen["kwargs"]["end"]-seen["kwargs"]["start"]).days==6
    assert result["profile"]=="AUTHENTICATED"
    assert result["history_bars"]==4
    assert result["trading_enabled"] is False
    assert "fingerprint" not in str(result).lower()
    assert "access_token" not in str(result)
    assert "last_price" not in str(result)


@pytest.mark.asyncio
async def test_market_probe_failure_is_generic(db,monkeypatch):
    from fastapi import HTTPException
    import intelligence.market_smoke as smoke
    monkeypatch.setenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","true")
    async def bad(*args,**kwargs):
        raise RuntimeError("secret-broker-response-DO-NOT-LEAK")
    monkeypatch.setattr(smoke,"check_stored_market_access",bad)
    with pytest.raises(HTTPException) as err:
        await fyers_test_market_data(user=SimpleNamespace(id=1),session=db)
    assert err.value.status_code==503
    assert "secret-broker-response" not in str(err.value.detail)


def test_staging_ui_exposes_readonly_probe_without_prices():
    html=(Path(__file__).resolve().parents[1]/"frontend/intelligence.html").read_text()
    assert 'id="probeFyersData"' in html
    assert "/v2/market/fyers/test-data" in html
    assert "Reauthorize" in html


@pytest.mark.asyncio
async def test_third_party_callback_uses_only_the_starting_login(db, monkeypatch):
    from intelligence import broker_oauth, fyers_session
    from intelligence.routes import broker_connection_status
    from main import _issue_token

    monkeypatch.setenv("AUTH_SECRET_KEY", "fixture-session-secret-at-least-32-bytes")
    monkeypatch.setenv("FINREVIEW_ENV", "production")
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_ENABLED", "true")
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "true")
    monkeypatch.setenv("FINREVIEW_FYERS_FRONTEND_RETURN_URL", "https://demo.example/finreview/intelligence.html")

    async def exchange(provider, code):
        assert provider == "fyers" and code == "fixture-code"
        return {"access_token": "private-fixture", "app_id": "fixture"}

    monkeypatch.setattr(broker_oauth, "exchange_code", exchange)
    user = SimpleNamespace(id=2)
    auth = "Bearer " + _issue_token(user.id)
    info = start_broker_authorization("fyers", user=user, session=db, authorization=auth)
    state = parse_qs(urlparse(info["authorization_url"]).query)["state"][0]
    response = await broker_authorization_callback(
        "fyers", request=SimpleNamespace(query_params={"auth_code": "fixture-code", "state": state}), session=db
    )
    assert response.status_code == 303
    assert response.headers["location"] == "https://demo.example/finreview/intelligence.html#fyersConnectionPanel"
    assert len(broker_connection_status(user=user, session=db, authorization=auth)["connections"]) == 2
    assert fyers_session.read(db, user.id, auth)["access_token"] == "private-fixture"
    other = "Bearer " + _issue_token(user.id)
    assert broker_connection_status(user=user, session=db, authorization=other)["connections"] == []
    assert broker_connection_status(user=SimpleNamespace(id=1), session=db, authorization=auth)["connections"] == []
    assert status(db, user.id)["connections"] == []


def test_third_party_requires_approval_and_complete_configuration(db, monkeypatch):
    """An enabled-but-unapproved integration must never expose a multi-user login."""
    from fastapi import HTTPException
    from intelligence import fyers_session

    monkeypatch.setenv("FINREVIEW_ENV", "production")
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_ENABLED", "true")
    monkeypatch.delenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", raising=False)
    monkeypatch.delenv("FINREVIEW_FYERS_FRONTEND_RETURN_URL", raising=False)
    assert not fyers_session.enabled()
    assert not fyers_session.configuration_ready()
    assert not fyers_test_availability(user=SimpleNamespace(id=2))["available"]
    with pytest.raises(HTTPException) as error:
        start_broker_authorization("fyers", user=SimpleNamespace(id=2), session=db)
    assert error.value.status_code == 403

    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "true")
    assert fyers_session.enabled()
    assert not fyers_session.configuration_ready()  # Return destination still missing
    assert not fyers_test_availability(user=SimpleNamespace(id=2))["available"]
    with pytest.raises(HTTPException) as error:
        start_broker_authorization("fyers", user=SimpleNamespace(id=2), session=db, authorization=None)
    assert error.value.status_code == 409

    monkeypatch.setenv("FINREVIEW_FYERS_FRONTEND_RETURN_URL", "https://example.org/intelligence.html")
    assert fyers_session.configuration_ready()
    assert fyers_test_availability(user=SimpleNamespace(id=2))["available"]

    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "false")
    assert not fyers_session.enabled()
    assert not fyers_test_availability(user=SimpleNamespace(id=2))["available"]


def test_third_party_readiness_rejects_callback_without_hostname(db, monkeypatch):
    from intelligence import fyers_session

    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_ENABLED", "true")
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "true")
    monkeypatch.setenv("FINREVIEW_FYERS_FRONTEND_RETURN_URL", "https://example.org/intelligence.html")
    monkeypatch.setenv("FINREVIEW_FYERS_REDIRECT_URI", "https://")
    assert not fyers_session.configuration_ready()

    monkeypatch.setenv("FINREVIEW_FYERS_REDIRECT_URI", "https://example.org/v2/market/oauth/fyers/callback")
    assert fyers_session.configuration_ready()
