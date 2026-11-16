import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from models.portable_models import User
from intelligence.broker_oauth import (
    BrokerAuthError, BrokerAuthAttempt, begin, claim_state, exchange_code, complete
)
from intelligence.credential_vault import status, read_trusted
from intelligence import fyers_session as flow

@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("FINREVIEW_BROKER_VAULT_KEY",Fernet.generate_key().decode())
    for p in ("FYERS","UPSTOX","ZERODHA"):
        monkeypatch.setenv("ZERODHA_API_KEY" if p=="ZERODHA" else p+"_CLIENT_ID","app123")
        monkeypatch.setenv("FYERS_APP_SECRET" if p=="FYERS" else
                           "ZERODHA_API_SECRET" if p=="ZERODHA" else "UPSTOX_CLIENT_SECRET","secret123")
        monkeypatch.setenv("FINREVIEW_"+p+"_REDIRECT_URI","https://example.org/oauth/callback")
    e=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        s.add(User(email="oauth123@example.com",password="test"))
        s.add(User(email="oauth456@example.com",password="test"))
        s.commit()
        yield s

@pytest.mark.parametrize("broker",["upstox","fyers","zerodha"])
def test_login_urls_and_single_use(db,broker):
    now=datetime.now(timezone.utc)
    result=begin(db,1,broker,now=now)
    parsed=urlparse(result["authorization_url"])
    assert parsed.hostname in ("api.upstox.com","api-t1.fyers.in","kite.zerodha.com")
    qs=parse_qs(parsed.query)
    state=(qs.get("state") or [parse_qs(qs["redirect_params"][0])["finreview_state"][0]])[0] if broker=="zerodha" else qs["state"][0]
    assert "secret123" not in str(result)
    assert claim_state(db,broker,state,now=now)==1
    with pytest.raises(BrokerAuthError):
        claim_state(db,broker,state,now=now)
    assert status(db,2)["connections"]==[]

def test_wrong_provider_and_expiry(db):
    now=datetime.now(timezone.utc)
    x=begin(db,1,"upstox",now=now)
    s=parse_qs(urlparse(x["authorization_url"]).query)["state"][0]
    with pytest.raises(BrokerAuthError):
        claim_state(db,"fyers",s,now=now)
    with pytest.raises(BrokerAuthError):
        claim_state(db,"upstox",s,now=now+timedelta(minutes=6))

@pytest.mark.asyncio
@pytest.mark.parametrize("broker",["upstox","fyers","zerodha"])
async def test_token_exchange_mocked_no_secret_leaks(db,broker):
    def handler(req):
        if broker=="upstox":
            assert req.url.path=="/v2/login/authorization/token"
            return httpx.Response(200,json={"access_token":"mock-token"})
        if broker=="fyers":
            assert req.url.path=="/api/v3/validate-authcode"
            return httpx.Response(200,json={"s":"ok","access_token":"mock-token"})
        assert req.url.path=="/session/token"
        return httpx.Response(200,json={"status":"success","data":{"access_token":"mock-token"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await exchange_code(broker,"temporary-code",client=client)
    assert result["access_token"]=="mock-token"

@pytest.mark.asyncio
async def test_complete_scopes_to_user(db):
    info=begin(db,2,"upstox")
    state=parse_qs(urlparse(info["authorization_url"]).query)["state"][0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
        httpx.Response(200,json={"access_token":"private-token"}))) as client:
        result=await complete(db,"upstox",state,"valid-code",client=client)
    assert result["state"]=="STORED_UNVERIFIED"
    assert status(db,1)["connections"]==[]
    assert len(status(db,2)["connections"])==2
    assert read_trusted(db,2,"upstox","quotes")["access_token"]=="private-token"
    with pytest.raises(BrokerAuthError):
        await complete(db,"upstox",state,"valid-code",client=client)

def test_pending_auth_attempts_are_bounded_per_account(db):
    now=datetime.now(timezone.utc)
    for _ in range(3):
        begin(db,1,"upstox",now=now)
    with pytest.raises(BrokerAuthError,match="Too many pending"):
        begin(db,1,"upstox",now=now)
    # Another user's authorization flow must remain independent.
    assert begin(db,2,"upstox",now=now)["status"]=="PENDING"
    # Old attempts cannot block a fresh flow forever.
    assert begin(db,1,"upstox",now=now+timedelta(minutes=6))["status"]=="PENDING"


@pytest.mark.asyncio
async def test_oauth_scope_write_failure_preserves_previous_session_and_consumes_state(db, monkeypatch):
    from intelligence import broker_oauth
    from intelligence.credential_vault import store_trusted

    for purpose in ("historical", "quotes"):
        store_trusted(db, 1, "fyers", purpose, {"access_token": "previous-token"})
    info = begin(db, 1, "fyers")
    state = parse_qs(urlparse(info["authorization_url"]).query)["state"][0]

    def fail_on_quotes(session, user_id, broker, purpose, credentials, **kwargs):
        if purpose == "quotes":
            raise RuntimeError("private storage error")
        return store_trusted(session, user_id, broker, purpose, credentials, **kwargs)

    monkeypatch.setattr(broker_oauth, "store_trusted", fail_on_quotes)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
        httpx.Response(200, json={"s": "ok", "access_token": "replacement-token"}))) as client:
        with pytest.raises(BrokerAuthError, match="could not be stored"):
            await complete(db, "fyers", state, "valid-code", client=client)
    for purpose in ("historical", "quotes"):
        assert read_trusted(db, 1, "fyers", purpose)["access_token"] == "previous-token"
    with pytest.raises(BrokerAuthError, match="already consumed"):
        claim_state(db, "fyers", state)


@pytest.mark.asyncio
@pytest.mark.parametrize("broker,payload", [("fyers", []), ("zerodha", {"status": "success", "data": None})])
async def test_invalid_broker_exchange_payload_is_a_controlled_error(db, broker, payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
        httpx.Response(200, json=payload))) as client:
        with pytest.raises(BrokerAuthError):
            await exchange_code(broker, "temporary-code", client=client)


def test_fyers_session_isolation_and_cancel(db, monkeypatch):
    from main import _issue_token

    monkeypatch.setenv("AUTH_SECRET_KEY", "fixture-session-secret-at-least-32-bytes")
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_ENABLED", "true")
    monkeypatch.setenv("FINREVIEW_FYERS_THIRD_PARTY_APPROVED", "true")
    monkeypatch.setenv("FINREVIEW_FYERS_FRONTEND_RETURN_URL", "https://example.org/intelligence.html")
    auth = "Bearer " + _issue_token(1)
    other_session = "Bearer " + _issue_token(1)
    assert auth != other_session
    state = parse_qs(urlparse(flow.begin(db, 1, auth)["authorization_url"]).query)["state"][0]
    claim = flow.claim(db, state)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(BrokerAuthError):
        flow.claim(db, state)
    flow.finish(db, claim, {"access_token": "fixture"}, future)
    assert flow.read(db, 1, auth)["access_token"] == "fixture"
    assert flow.status(db, 1, other_session)["connections"] == []
    assert flow.status(db, 2, auth)["connections"] == []
    flow.disconnect(db, 1, other_session)
    assert flow.read(db, 1, auth)["access_token"] == "fixture"
    flow.disconnect(db, 1, auth)
    with pytest.raises(BrokerAuthError):
        flow.read(db, 1, auth)

    state = parse_qs(urlparse(flow.begin(db, 1, auth)["authorization_url"]).query)["state"][0]
    claim = flow.claim(db, state)
    flow.disconnect(db, 1, auth)
    with pytest.raises(BrokerAuthError):
        flow.finish(db, claim, {"access_token": "fixture"}, future)
