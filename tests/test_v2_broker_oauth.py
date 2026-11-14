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
