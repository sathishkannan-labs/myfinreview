import sys
from pathlib import Path
from datetime import date
import pytest
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from intelligence.market_data import validate_candles, parse_csv_history, MarketDataError
from intelligence.upstox_data import UpstoxReadOnlyData
from intelligence.providers import load_history, available_providers

ROWS = [
    ["2025-01-03T15:30:00+05:30", 101, 104, 100, 103, 500, 0],
    ["2025-01-02T15:30:00+05:30", 100, 102, 99, 101, 400, 0],
]

def test_candle_validation_normalizes_and_sorts():
    candles=validate_candles(ROWS)
    assert len(candles)==2
    assert candles[0].close==101
    assert candles[0].timestamp.isoformat().endswith("+00:00")

@pytest.mark.parametrize("bad", [
    [["2025-01-01T00:00:00+05:30", 100, 90, 95, 97, 10]],
    [["2025-01-01T00:00:00+05:30", 100, 110, 95, 97, -1]],
    [["2025-01-01T00:00:00", 100, 110, 95, 97, 10]],
    [ROWS[0], ROWS[0]],
])
def test_rejects_invalid_prices_dates_duplicates(bad):
    with pytest.raises(MarketDataError):
        validate_candles(bad)

def test_csv_import():
    raw=b"timestamp,open,high,low,close,volume\n2025-01-01T15:30:00+05:30,100,105,98,104,500\n"
    bars=parse_csv_history(raw)
    assert len(bars)==1 and bars[0].volume==500
    with pytest.raises(MarketDataError):
        parse_csv_history(b"timestamp,open\n2025-01-01,1\n")

@pytest.mark.asyncio
async def test_upstox_v3_readonly_history_uses_token_and_path():
    seen=[]
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"status":"success", "data":{"candles":ROWS}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        p=UpstoxReadOnlyData(token="fake-test-token",http_client=client)
        data=await p.history("NSE_EQ|INE848E01016",date(2025,1,1),date(2025,1,31))
        assert len(data)==2
        assert "historical-candle" in str(seen[0].url)
        assert seen[0].headers["Authorization"] == "Bearer fake-test-token"
        assert "%7C" in str(seen[0].url)

@pytest.mark.asyncio
async def test_provider_normalization_with_mock_upstox():
    class Stub:
        async def history(self,*args):
            return validate_candles(ROWS)
    d=await load_history("upstox","NSE_EQ|INE848E01016",
                         date(2025,1,1), date(2025,1,31),upstox=Stub())
    assert d.provider=="upstox" and d.adjustment=="provider_raw_unverified"
    assert d.as_json()["candles"][0]["close"]==101

def test_provider_is_opt_in(monkeypatch):
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    monkeypatch.delenv("FYERS_CLIENT_ID", raising=False)
    monkeypatch.delenv("FYERS_ACCESS_TOKEN", raising=False)
    assert available_providers()=={"upstox":False,"fyers":False,"csv":True}
