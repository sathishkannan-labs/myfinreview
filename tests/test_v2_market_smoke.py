import sys
from datetime import date
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from intelligence.market_smoke import check_market_access, make_client, check_stored_market_access
from intelligence.market_data import MarketDataError, validate_candles

class History:
    async def history(self,*args):
        return validate_candles([["2026-10-08T15:30:00+05:30",100,110,99,105,200]])
class FyersHistory:
    async def history(self,*args):
        return {"candles":[[1791453600,100,110,99,105,200]]}
class QuoteClient:
    async def quotes(self,*args):
        return {"NSE:INFY":{"last_price":120.0}}
class FyersQuotes:
    async def quotes(self,*args):
        return {"d":[{"n":"NSE:INFY-EQ","s":"ok","v":{"lp":120.0}}]}
class UpQuotes:
    async def quotes(self,*args):
        return {"NSE_EQ:INFY":{"instrument_token":"NSE_EQ|INE009A01021","last_price":120.0}}

@pytest.mark.asyncio
@pytest.mark.parametrize("broker,hist,quote,hclient,qclient",[
    ("upstox","NSE_EQ|INE009A01021","NSE_EQ|INE009A01021",History(),UpQuotes()),
    ("fyers","NSE:INFY-EQ","NSE:INFY-EQ",FyersHistory(),FyersQuotes()),
    ("zerodha","408065","NSE:INFY",History(),QuoteClient()),
])
async def test_broker_sample_without_live_http(monkeypatch,broker,hist,quote,hclient,qclient):
    import intelligence.market_smoke as m
    async def fake_profile(provider, credentials, *, client=None):
        assert credentials["access_token"]=="test-only"
        return {"identity_fingerprint":"f"*64}
    monkeypatch.setattr(m,"probe_credentials",fake_profile)
    credentials={"access_token":"test-only","app_id":"test-app"}
    result=await check_market_access(broker,profile_credentials=credentials,
        historical_credentials=credentials,quote_credentials=credentials,
        history_instrument=hist,quote_instrument=quote,
        start=date(2026,10,8),end=date(2026,10,9),
        history_client=hclient,quote_client=qclient)
    assert result.profile=="AUTHENTICATED"
    assert result.quotes=="VERIFIED_SAMPLE"
    assert result.trading_enabled is False
    assert "test-only" not in str(result.as_json())
    assert quote not in str(result.as_json())

@pytest.mark.asyncio
async def test_failed_profile_stops_market_calls(monkeypatch):
    import intelligence.market_smoke as m
    async def bad(*args,**kwargs):
        raise MarketDataError("wrong broker credentials")
    monkeypatch.setattr(m,"probe_credentials",bad)
    result=await check_market_access("fyers",
       profile_credentials={},historical_credentials={},quote_credentials={},
       history_instrument="NSE:INFY-EQ",quote_instrument="NSE:INFY-EQ",
       start=date(2026,10,8),end=date(2026,10,9))
    assert (result.profile,result.history,result.quotes)==("FAILED","NOT_RUN","NOT_RUN")

@pytest.mark.asyncio
async def test_default_disabled(monkeypatch):
    monkeypatch.delenv("FINREVIEW_V2_BROKER_PROBES_ENABLED",raising=False)
    with pytest.raises(MarketDataError,match="disabled"):
        await check_stored_market_access(None,1,"fyers",history_instrument="x",
                quote_instrument="y",start=date(2026,10,8),end=date(2026,10,9))

def test_client_must_use_explicit_scoped_credentials(monkeypatch):
    monkeypatch.setenv("FYERS_ACCESS_TOKEN","environment-token-must-not-be-used")
    with pytest.raises(MarketDataError):
        make_client("fyers",{"app_id":"a"})
