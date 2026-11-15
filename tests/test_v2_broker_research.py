"""Owner-only broker research uses real adapter shapes and never writes to DB."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from intelligence.broker_research import research_from_fyers, INSTRUMENTS
from intelligence.routes import fyers_personal_research
from intelligence.market_data import MarketDataError

NOW=datetime(2026,11,15,6,30,tzinfo=timezone.utc)
class History:
    async def history(self, symbol, resolution, frm, to):
        assert symbol in INSTRUMENTS
        assert resolution=="D"
        # 100 completed daily candles, one per day, FYERS UNIX epoch.
        rows=[]
        start=NOW-timedelta(days=110)
        for i in range(100):
            ts=int((start+timedelta(days=i)).timestamp())
            o=100+i
            rows.append([ts,o,o+2,o-2,o+1,1200])
        return {"s":"ok","candles":rows}
class Quotes:
    async def quotes(self,symbols):
        return {"s":"ok","d":[{"n":symbols[0],"s":"ok","v":{"lp":236.4}}]}
@pytest.mark.asyncio
async def test_real_data_shaped_history_to_deterministic_strategy(monkeypatch):
    import intelligence.broker_research as research
    scopes=[]
    def fake_read(session, uid, provider,purpose):
        scopes.append((uid,provider,purpose))
        return {"app_id":"fake","access_token":"SECRET_FOR_TEST"}
    monkeypatch.setattr(research,"read_trusted",fake_read)
    async def valid(symbol):return symbol=="NSE:INFY-EQ"
    monkeypatch.setattr(research,"is_current_equity",valid)
    # Clients are injected so no actual network call occurs.
    result=await research_from_fyers(object(),7,"NSE:INFY-EQ",
        now=NOW,history_client=History(),quote_client=Quotes())
    assert scopes==[(7,"fyers","historical"),(7,"fyers","quotes")]
    assert result["status"]=="READY"
    assert result["rows"]>=50
    assert result["strategies"]["sma_trend"]["status"]=="READY"
    assert result["strategies"]["breakout"]["status"]=="READY"
    assert result["quote"]["last_price"]==236.4
    assert result["quote"]["timestamp_verified"] is False
    assert result["source"]=="FYERS_PERSONAL_ACCOUNT"
    assert result["stored"] is False and result["trading_enabled"] is False
    assert result["data_rights"]=="PERSONAL_ONLY_NOT_FOR_REDISTRIBUTION"
    assert "SECRET_FOR_TEST" not in str(result)
    assert "credentials" not in result
    assert result["last_timestamp"]!=result["quote"]["received_at"]
@pytest.mark.asyncio
async def test_invalid_instrument_prevents_vault_access(monkeypatch):
    import intelligence.broker_research as research
    def should_not_read(*args):
        raise AssertionError("No credential read for unallowlisted instrument")
    monkeypatch.setattr(research,"read_trusted",should_not_read)
    async def invalid(symbol):return False
    monkeypatch.setattr(research,"is_current_equity",invalid)
    with pytest.raises(MarketDataError):
        await research_from_fyers(None,1,"NSE:UNSUPPORTED-EQ",now=NOW)
@pytest.mark.asyncio
async def test_route_denies_non_owner_or_disabled(monkeypatch):
    monkeypatch.setenv("FINREVIEW_ENV","staging")
    monkeypatch.setenv("FINREVIEW_V2_OAUTH_ENABLED","true")
    monkeypatch.setenv("FINREVIEW_FYERS_TEST_USER_ID","1")
    monkeypatch.setenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","true")
    with pytest.raises(HTTPException) as e:
        await fyers_personal_research("NSE:INFY-EQ",user=SimpleNamespace(id=2),session=None)
    assert e.value.status_code==403
    monkeypatch.setenv("FINREVIEW_ENV","production")
    with pytest.raises(HTTPException) as e:
        await fyers_personal_research("NSE:INFY-EQ",user=SimpleNamespace(id=1),session=None)
    assert e.value.status_code==403

@pytest.mark.asyncio
async def test_route_no_store_and_scrubbed_failure(monkeypatch):
    import intelligence.broker_research as research
    monkeypatch.setenv("FINREVIEW_ENV","staging")
    monkeypatch.setenv("FINREVIEW_V2_OAUTH_ENABLED","true")
    monkeypatch.setenv("FINREVIEW_FYERS_TEST_USER_ID","1")
    monkeypatch.setenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","true")
    async def ok(*args,**kwargs):
        return {"status":"READY","source":"FYERS_PERSONAL_ACCOUNT","stored":False}
    monkeypatch.setattr(research,"research_from_fyers",ok)
    response=await fyers_personal_research("NSE:INFY-EQ",user=SimpleNamespace(id=1),session=None)
    assert response.headers["cache-control"]=="no-store, private"
    assert b"access_token" not in response.body
    async def bad(*args,**kwargs):
        raise RuntimeError("SECRET_BROKER_EXCEPTION")
    monkeypatch.setattr(research,"research_from_fyers",bad)
    with pytest.raises(HTTPException) as err:
        await fyers_personal_research("NSE:INFY-EQ",user=SimpleNamespace(id=1),session=None)
    assert err.value.status_code==503
    assert "SECRET_BROKER" not in err.value.detail
