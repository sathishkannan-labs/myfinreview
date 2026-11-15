import sys
from pathlib import Path
from datetime import date
import pytest
import httpx
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from models.portable_models import User
from intelligence.zerodha_data import ZerodhaReadOnlyData
from intelligence.market_data import MarketDataError
from intelligence.providers import load_history, available_providers
from intelligence.provider_preferences import choose_brokers, selected_brokers

@pytest.mark.asyncio
async def test_kite_historical_read_only_and_normalized():
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={"status":"success","data":{"candles":[
            ["2026-10-08T15:30:00+05:30",100,110,98,104,500,0]]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        kite=ZerodhaReadOnlyData(api_key="demo_key",access_token="demo_token",http_client=client)
        d=await load_history("zerodha","408065",date(2026,10,1),date(2026,10,15),zerodha=kite)
        assert len(d.candles)==1 and d.candles[0].close==104
        assert d.provider=="zerodha"
        assert "/instruments/historical/408065/day" in str(seen[0].url)
        assert seen[0].headers["Authorization"]=="token demo_key:demo_token"
        assert seen[0].headers["X-Kite-Version"]=="3"

@pytest.mark.asyncio
async def test_kite_ltp_quotes_only():
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={"status":"success","data":{"NSE:INFY":{"last_price":1500}}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        kite=ZerodhaReadOnlyData(api_key="x",access_token="y",http_client=client)
        quotes=await kite.quotes(["NSE:INFY"])
        assert quotes["NSE:INFY"]["last_price"]==1500
        assert str(seen[0].url).count("i=")==1
        assert "/quote/ltp" in str(seen[0].url)
        with pytest.raises(MarketDataError):
            await kite.quotes(["bad symbol"])

def test_preferences_isolate_users_and_store_no_credentials():
    engine=create_engine("sqlite://",poolclass=StaticPool,connect_args={"check_same_thread":False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(email="zerodha1@example.com",password="x"))
        session.add(User(email="zerodha2@example.com",password="x"))
        session.commit()
        choose_brokers(session,1,"fyers","zerodha")
        assert selected_brokers(session,1)["quotes_provider"]=="zerodha"
        assert selected_brokers(session,2)["quotes_provider"] is None
        choose_brokers(session,1,"upstox","upstox")
        assert selected_brokers(session,1)["historical_provider"]=="upstox"
        with pytest.raises(MarketDataError):
            choose_brokers(session,1,"unknown","fyers")
        assert "token" not in str(selected_brokers(session,1)).lower()

def test_all_three_are_independent(monkeypatch):
    monkeypatch.setenv("ZERODHA_API_KEY","x")
    monkeypatch.setenv("ZERODHA_ACCESS_TOKEN","y")
    monkeypatch.delenv("FYERS_CLIENT_ID",raising=False)
    monkeypatch.delenv("FYERS_ACCESS_TOKEN",raising=False)
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN",raising=False)
    options=available_providers()
    assert options["zerodha"] is True
    assert options["fyers"] is False and options["upstox"] is False
    assert options["csv"] is True
