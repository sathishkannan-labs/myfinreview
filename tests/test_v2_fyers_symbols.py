"""FYERS symbol-master autocomplete: fixed source, owner scope, safe matching."""
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from intelligence import fyers_symbols
from intelligence.routes import fyers_personal_symbols
from intelligence.market_data import MarketDataError

SAMPLE={
 "NSE:INFY-EQ":{"description":"INFOSYS LIMITED","tradeStatus":1},
 "NSE:RELIANCE-EQ":{"description":"RELIANCE INDUSTRIES LTD","tradeStatus":1},
 "NSE:SBIN-EQ":{"description":"STATE BANK OF INDIA","tradeStatus":1},
 "NSE:OLD-EQ":{"description":"SUSPENDED STOCK","tradeStatus":0},
 "NSE:NIFTY50-INDEX":{"description":"INDEX","tradeStatus":1},
 "NSE:NIFTY26NOVFUT":{"description":"FUTURE","tradeStatus":1},
 "BSE:SBIN-A":{"description":"OTHER EXCHANGE","tradeStatus":1},
}

def test_parser_keeps_nse_active_eq_only():
    entries=fyers_symbols.parse_master(SAMPLE)
    assert set(entries)=={"NSE:INFY-EQ","NSE:RELIANCE-EQ","NSE:SBIN-EQ"}
    assert entries["NSE:SBIN-EQ"]=="STATE BANK OF INDIA"

@pytest.mark.asyncio
async def test_company_and_ticker_search(monkeypatch):
    async def mock_catalog():
        return fyers_symbols.parse_master(SAMPLE)
    monkeypatch.setattr(fyers_symbols,"catalog",mock_catalog)
    assert (await fyers_symbols.search_symbols("bank"))[0]["symbol"]=="NSE:SBIN-EQ"
    assert (await fyers_symbols.search_symbols("rel"))[0]["symbol"]=="NSE:RELIANCE-EQ"
    assert await fyers_symbols.search_symbols("z")==[]
    assert await fyers_symbols.is_current_equity("NSE:INFY-EQ")
    assert not await fyers_symbols.is_current_equity("NSE:OLD-EQ")
    assert not await fyers_symbols.is_current_equity("NSE:INVALID-INDEX")

@pytest.mark.asyncio
async def test_search_is_owner_only(monkeypatch):
    monkeypatch.setenv("FINREVIEW_ENV","staging")
    monkeypatch.setenv("FINREVIEW_V2_OAUTH_ENABLED","true")
    monkeypatch.setenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","true")
    monkeypatch.setenv("FINREVIEW_FYERS_TEST_USER_ID","1")
    with pytest.raises(HTTPException) as e:
        await fyers_personal_symbols("inf",user=SimpleNamespace(id=2))
    assert e.value.status_code==403
    async def stub(query):
        assert query=="inf"
        return [{"symbol":"NSE:INFY-EQ","name":"INFOSYS LIMITED"}]
    monkeypatch.setattr(fyers_symbols,"search_symbols",stub)
    response=await fyers_personal_symbols("inf",user=SimpleNamespace(id=1))
    assert response.headers["cache-control"]=="no-store, private"
    assert b"NSE:INFY-EQ" in response.body
    monkeypatch.setenv("FINREVIEW_ENV","production")
    with pytest.raises(HTTPException) as e:
        await fyers_personal_symbols("inf",user=SimpleNamespace(id=1))
    assert e.value.status_code==403

def test_search_ui_includes_keyboard_and_explicit_selection():
    html=(Path(__file__).resolve().parents[1]/"frontend/intelligence.html").read_text()
    assert 'id="fyersSymbolMatches"' in html
    assert 'role="combobox"' in html
    assert "/v2/market/fyers/symbols" in html
    assert "fyersSelected.value=row.symbol" in html
    assert "event.key==='ArrowDown'" in html
    assert "Choose a matching equity" in html
    assert "Dashboard &amp; Portfolio" in html
