import os
import sys
from pathlib import Path
from datetime import date
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from intelligence.routes import historical_market_data, market_providers
from intelligence.market_data import MarketDataError

@pytest.mark.asyncio
async def test_market_route_off_by_default(monkeypatch):
    monkeypatch.delenv("FINREVIEW_V2_MARKET_API_ENABLED", raising=False)
    with pytest.raises(HTTPException) as err:
        await historical_market_data("upstox","NSE_EQ|INE848E01016",date(2025,1,1),date(2025,1,31),user=object())
    assert err.value.status_code == 403

@pytest.mark.asyncio
async def test_market_route_length_restriction(monkeypatch):
    monkeypatch.setenv("FINREVIEW_V2_MARKET_API_ENABLED","true")
    with pytest.raises(HTTPException) as err:
        await historical_market_data("upstox","NSE_EQ|INE848E01016",date(2024,1,1),date(2025,2,1),user=object())
    assert err.value.status_code == 422

def test_provider_status_never_returns_secret(monkeypatch):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN","private-secret")
    status=market_providers(user=object())
    assert status["available"]["upstox"] is True
    assert "private-secret" not in str(status)
