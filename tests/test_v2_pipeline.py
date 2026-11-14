from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import sys
import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from models.portable_models import User
from intelligence.market_data import Candle
from intelligence.candle_cache import CachedCandle
from intelligence.models import StrategySignal
from intelligence.pipeline import synchronize_and_research, evaluate_cached_research

IST=ZoneInfo("Asia/Kolkata")

@pytest.fixture
def session():
    engine=create_engine("sqlite://",poolclass=StaticPool,connect_args={"check_same_thread":False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(User(email="pipeline@example.com",password="test"))
        s.add(User(email="pipelineother@example.com",password="test"))
        s.commit()
        yield s

def bars_at(now):
    # 55 prior calendar days; this is mocked data only, not a market calendar.
    today=now.astimezone(IST).date()
    candles=[]
    for i in range(55):
        d=today-timedelta(days=55-i)
        t=datetime.combine(d,datetime.min.time(),IST).replace(hour=15,minute=30)
        end=i==54
        close=120.0 if end else 100.0
        candles.append(Candle(t.astimezone(timezone.utc),close,
                              121.0 if end else 101.0,99.0,close,1000.0))
    return tuple(candles)

@pytest.mark.asyncio
async def test_pipeline_preview_is_idempotent_and_account_isolated(session):
    now=datetime(2026,11,11,12,0,tzinfo=IST).astimezone(timezone.utc)
    source=bars_at(now)
    class Provider:
        async def history(self,*args):
            return list(source)
    a=await synchronize_and_research(session,1,"upstox","NSE_EQ|TEST",
        date(2026,9,1),date(2026,11,11),now=now,client=Provider())
    assert a.inserted==55 and a.outcome=="CANDIDATE_PREVIEW"
    b=await synchronize_and_research(session,1,"upstox","NSE_EQ|TEST",
        date(2026,9,1),date(2026,11,11),now=now,client=Provider())
    assert b.inserted==0 and b.unchanged==55
    assert session.exec(select(StrategySignal)).all()==[]
    assert evaluate_cached_research(session,2,"upstox","NSE_EQ|TEST",now=now).outcome=="INSUFFICIENT_DATA"

@pytest.mark.asyncio
async def test_pipeline_persist_watching_once(session):
    now=datetime(2026,11,11,12,0,tzinfo=IST).astimezone(timezone.utc)
    source=bars_at(now)
    class Provider:
        async def history(self,*args):
            return list(source)
    one=await synchronize_and_research(session,1,"upstox","NSE_EQ|TEST",
        date(2026,9,1),date(2026,11,11),now=now,client=Provider(),publish_watch=True)
    two=await synchronize_and_research(session,1,"upstox","NSE_EQ|TEST",
        date(2026,9,1),date(2026,11,11),now=now,client=Provider(),publish_watch=True)
    assert one.outcome=="WATCHING_CREATED"
    assert two.outcome=="ALREADY_RECORDED"
    signal=session.exec(select(StrategySignal)).one()
    assert signal.status=="WATCHING" and signal.entry_price is None
    assert "PRELIMINARY" in signal.rationale

@pytest.mark.asyncio
async def test_pipeline_weekend_does_not_publish(session):
    now=datetime(2026,11,14,12,0,tzinfo=IST).astimezone(timezone.utc)
    source=bars_at(now)
    class Provider:
        async def history(self,*args):
            return list(source)
    r=await synchronize_and_research(session,1,"upstox","NSE_EQ|TEST",
        date(2026,9,1),date(2026,11,14),now=now,client=Provider(),publish_watch=True)
    assert r.outcome=="OUTSIDE_MONITORING_WINDOW"
    assert session.exec(select(StrategySignal)).all()==[]
