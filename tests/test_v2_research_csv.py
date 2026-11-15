"""Research CSV stays useful without broker accounts and cannot create orders."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from intelligence.research_csv import analyze_csv_bytes
from intelligence.market_data import MarketDataError
from intelligence.routes import research_uploaded_csv

NOW=datetime(2026,11,14,12,0,tzinfo=timezone.utc)

def csv_rows(n=55, *, start=datetime(2026,8,1,10,0,tzinfo=timezone.utc)):
    out=["timestamp,open,high,low,close,volume"]
    for i in range(n):
        stamp=(start+timedelta(days=i)).isoformat()
        base=100+i*0.5
        out.append(f"{stamp},{base},{base+2},{base-2},{base+1},1000")
    return ("\n".join(out)+"\n").encode()

def test_csv_research_is_deterministic_private_and_nontrading():
    result=analyze_csv_bytes(csv_rows(),now=NOW)
    assert result["status"]=="READY"
    assert result["rows"]==55
    assert result["strategies"]["sma_trend"]["status"]=="READY"
    assert result["strategies"]["breakout"]["status"]=="READY"
    assert result["stored"] is False
    assert result["trading_enabled"] is False
    assert result["provider_authenticated"] is False
    assert result["adjustment"]=="UNVERIFIED"

def test_short_csv_admits_no_strategy_hallucinations():
    result=analyze_csv_bytes(csv_rows(3),now=NOW)
    assert result["status"]=="INSUFFICIENT_DATA"
    assert result["strategies"]["breakout"]["observation"] is None
    assert result["strategies"]["sma_trend"]["observation"] is None

def test_rejects_current_day_and_duplicate_daily_candles():
    today=NOW.replace(hour=10)
    with pytest.raises(MarketDataError,match="current-day"):
        analyze_csv_bytes(csv_rows(1,start=today),now=NOW)
    same_day=csv_rows(1)+("2026-08-01T12:00:00+00:00,100,102,98,101,1000\n").encode()
    with pytest.raises(MarketDataError,match="multiple candles"):
        analyze_csv_bytes(same_day,now=NOW)

class RequestStub:
    def __init__(self,body):self.body=body
    async def stream(self):
        yield self.body

@pytest.mark.asyncio
async def test_research_route_is_read_only_and_rejects_oversized():
    result=await research_uploaded_csv(RequestStub(csv_rows()),user=object())
    assert result["source"]=="USER_UPLOADED_CSV"
    with pytest.raises(HTTPException) as exc:
        await research_uploaded_csv(RequestStub(b"x"*3_000_001),user=object())
    assert exc.value.status_code==413

def test_research_ui_discoverable_and_broker_optional():
    html=(Path(__file__).resolve().parents[1]/"frontend/intelligence.html").read_text()
    for phrase in ("id=\"researchCsvForm\"","/v2/market/csv/research",
                   "id=\"initializePaperAccount\"","No broker account required"):
        assert phrase.lower() in html.lower()
