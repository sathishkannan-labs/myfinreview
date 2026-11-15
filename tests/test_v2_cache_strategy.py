from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from models.portable_models import User
from intelligence.market_data import validate_candles, MarketDataError
from intelligence.candle_cache import CachedCandle, save_candles, read_candles
from intelligence.strategies import breakout_observation, sma_trend_observation, atr_at_end

@pytest.fixture
def session():
    e=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        s.add(User(email="first@example.com",password="test"))
        s.add(User(email="second@example.com",password="test"))
        s.commit()
        yield s

def fixture_bars(n=55, last_close=120):
    start=datetime(2025,1,1,tzinfo=timezone.utc)
    raw=[]
    for i in range(n):
        close=100 if i < n-1 else last_close
        raw.append([int((start+timedelta(days=i)).timestamp()),close, max(close+1,101),
                    min(close-1,99),close,1000])
    return validate_candles(raw)

def test_account_isolation_and_cache_idempotency(session):
    bars=fixture_bars(30)
    assert save_candles(session,1,"csv","TEST",bars)["inserted"] == 30
    assert save_candles(session,1,"csv","TEST",bars)["unchanged"] == 30
    start,end=bars[0].timestamp,bars[-1].timestamp
    assert read_candles(session,2,"csv","TEST",start,end)["count"] == 0
    result=read_candles(session,1,"csv","TEST",start,end)
    assert result["count"] == 30
    assert result["coverage"] == "unverified_partial"

def test_revised_candle_rejected_atomically(session):
    bars=fixture_bars(30)
    save_candles(session,1,"upstox","TEST",bars)
    wrong=list(bars)
    row=wrong[-1]
    from intelligence.market_data import Candle
    wrong[-1]=Candle(row.timestamp,row.open,row.high+3,row.low,row.close,row.volume)
    with pytest.raises(MarketDataError,match="revised"):
        save_candles(session,1,"upstox","TEST",wrong)
    assert read_candles(session,1,"upstox","TEST",bars[0].timestamp,bars[-1].timestamp)["count"]==30

def test_breakout_excludes_current_bar():
    bars=fixture_bars(55,120)
    signal=breakout_observation(bars)
    assert signal.status == "CANDIDATE"
    assert signal.trigger == 101
    assert signal.invalidation is not None
    assert signal.as_of == bars[-1].timestamp
    assert breakout_observation(bars[:-1]).status == "WATCHING"

def test_sma_and_atr_are_deterministic():
    bars=fixture_bars(55,120)
    assert atr_at_end(bars) > 0
    assert sma_trend_observation(bars).status == "CANDIDATE"
    assert breakout_observation(bars[:10]) is None

def test_wrong_order_fails_closed():
    with pytest.raises(MarketDataError,match="chronological"):
        breakout_observation(list(reversed(fixture_bars(55))))
