import os
import sys
from pathlib import Path
import pytest
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from intelligence.market_data import MarketDataError
from intelligence.session_probe import probe_credentials, probe_stored

@pytest.mark.asyncio
@pytest.mark.parametrize("provider,response",[
    ("fyers",{"s":"ok","data":{"fy_id":"FY000001","email_id":"secret@example.com"}}),
    ("upstox",{"status":"success","data":{"broker":"UPSTOX","user_id":"U123","mobile":"secret"}}),
    ("zerodha",{"status":"success","data":{"broker":"ZERODHA","user_id":"Z123","email":"secret@example.com"}}),
])
async def test_read_only_profile_does_not_leak_pii(monkeypatch,provider,response):
    monkeypatch.setenv("FINREVIEW_BROKER_IDENTITY_HMAC_KEY","sufficiently-long-private-key-for-testing")
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json=response)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result=await probe_credentials(provider,{"access_token":"private-value","app_id":"app123"},client=http)
    assert result["profile_authenticated"] is True
    assert result["historical_entitlement"]=="UNVERIFIED"
    assert result["quotes_entitlement"]=="UNVERIFIED"
    assert len(result["identity_fingerprint"])==64
    assert all(k not in str(result) for k in ("private-value","FY000001","secret@example.com","U123","Z123"))
    assert seen[0].method=="GET"
    assert "/user/profile" in str(seen[0].url) or "/profile" in str(seen[0].url)

@pytest.mark.asyncio
async def test_broker_identity_mismatch_rejected(monkeypatch):
    monkeypatch.setenv("FINREVIEW_BROKER_IDENTITY_HMAC_KEY","sufficiently-long-private-key-for-testing")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r:httpx.Response(200,json={"status":"success","data":{"broker":"UPSTOX","user_id":"123"}})
    )) as http:
        with pytest.raises(MarketDataError):
            await probe_credentials("zerodha",{"access_token":"secret","app_id":"app"},client=http)

@pytest.mark.asyncio
async def test_probe_requires_private_hmac_key(monkeypatch):
    monkeypatch.delenv("FINREVIEW_BROKER_IDENTITY_HMAC_KEY",raising=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r:httpx.Response(200,json={"s":"ok","data":{"fy_id":"F123"}})
    )) as http:
        with pytest.raises(MarketDataError):
            await probe_credentials("fyers",{"access_token":"secret","app_id":"app"},client=http)

@pytest.mark.asyncio
async def test_probe_stored_disabled_without_optin(monkeypatch):
    monkeypatch.delenv("FINREVIEW_V2_BROKER_PROBES_ENABLED",raising=False)
    with pytest.raises(MarketDataError,match="disabled"):
        await probe_stored(None,1,"upstox","quotes")
