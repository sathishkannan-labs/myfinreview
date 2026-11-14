import sys
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from sqlmodel import SQLModel, Session, select, create_engine
from sqlalchemy.pool import StaticPool
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from models.portable_models import User
from intelligence.models import NotificationDelivery, StrategySignal, SignalEvent
from intelligence.scheduled_reports import approved_sessions, due_slot, expire_signals, run_report

IST = ZoneInfo("Asia/Kolkata")
TUES = datetime(2026, 11, 17, 12, 1, tzinfo=IST)

@pytest.fixture
def db():
    e=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        s.add(User(email="digest1@example.com", password="test"))
        s.add(User(email="digest2@example.com", password="test"))
        s.commit()
        yield s

def test_calendar_fail_closed_and_ist_slots():
    sessions=approved_sessions("2026-11-17,2026-11-18")
    assert due_slot(TUES,sessions=sessions) == ("midday",date(2026,11,17))
    assert due_slot(TUES-timedelta(minutes=2),sessions=sessions) is None
    assert due_slot(TUES+timedelta(minutes=5),sessions=sessions) is None
    assert due_slot(TUES,sessions=set()) is None
    assert due_slot(datetime(2026,11,14,12,0,tzinfo=IST),sessions=sessions) is None
    with pytest.raises(ValueError):
        approved_sessions("2026-11-14")
    with pytest.raises(ValueError):
        due_slot(datetime(2026,11,17,12,1),sessions=sessions)

@pytest.mark.asyncio
async def test_dry_run_no_write_no_delivery(db, monkeypatch):
    called=[]
    async def sender(text):
        called.append(text)
    monkeypatch.setenv("FINREVIEW_V2_TELEGRAM_ENABLED","true")
    r=await run_report(db,1,now=TUES,sessions={TUES.date()},sender=sender)
    assert r.state=="DRY_RUN" and "12:00" in r.message
    assert called==[]
    assert db.exec(select(NotificationDelivery)).all()==[]

@pytest.mark.asyncio
async def test_live_opt_in_and_deduplication(db, monkeypatch):
    sent=[]
    async def sender(text):
        sent.append(text)
    monkeypatch.delenv("FINREVIEW_V2_TELEGRAM_ENABLED",raising=False)
    r=await run_report(db,1,now=TUES,sessions={TUES.date()},dry_run=False,sender=sender)
    assert r.state=="DISABLED" and sent==[]
    monkeypatch.setenv("FINREVIEW_V2_TELEGRAM_ENABLED","true")
    r=await run_report(db,1,now=TUES,sessions={TUES.date()},dry_run=False,sender=sender)
    assert r.state=="SENT" and len(sent)==1
    r=await run_report(db,1,now=TUES,sessions={TUES.date()},dry_run=False,sender=sender)
    assert r.state=="ALREADY_CLAIMED" and len(sent)==1
    assert db.exec(select(NotificationDelivery)).one().status=="SENT"

@pytest.mark.asyncio
async def test_ambiguous_delivery_is_not_retried(db, monkeypatch):
    monkeypatch.setenv("FINREVIEW_V2_TELEGRAM_ENABLED","true")
    called=[]
    async def sender(text):
        called.append(text)
        raise TimeoutError("secrets cannot be stored")
    r=await run_report(db,1,now=TUES,sessions={TUES.date()},dry_run=False,sender=sender)
    assert r.state=="UNKNOWN"
    assert (await run_report(db,1,now=TUES,sessions={TUES.date()},
                             dry_run=False,sender=sender)).state=="ALREADY_CLAIMED"
    assert len(called)==1
    assert "secrets" not in str(db.exec(select(NotificationDelivery)).one().error)

def test_expiry_is_account_scoped_and_idempotent(db):
    now=TUES.astimezone(timezone.utc)
    db.add(StrategySignal(user_id=1,symbol="NSE:AAA-EQ",strategy="test",source_key="a",
            reference_price=100,created_at=now-timedelta(hours=2),
            data_timestamp=now-timedelta(hours=2),expires_at=now-timedelta(minutes=1)))
    db.add(StrategySignal(user_id=2,symbol="NSE:BBB-EQ",strategy="test",source_key="b",
            reference_price=100,created_at=now-timedelta(hours=2),
            data_timestamp=now-timedelta(hours=2),expires_at=now-timedelta(minutes=1)))
    db.commit()
    assert expire_signals(db,1,now)==1
    assert expire_signals(db,1,now)==0
    assert len(db.exec(select(SignalEvent)).all())==1
    assert db.exec(select(StrategySignal).where(StrategySignal.user_id==2)).one().status=="WATCHING"
