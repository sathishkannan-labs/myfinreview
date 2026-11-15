import os, sys
from pathlib import Path
from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from models.portable_models import User
from intelligence.models import StrategySignal, PaperTrade, utc_now
from intelligence.service import create_signal, transition, effective_status, list_signals, create_paper_account, paper_fill
from intelligence.reporting import daily_digest

@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(email="v2one@example.com", password="test"))
        session.add(User(email="v2two@example.com", password="test"))
        session.commit()
        yield session

def test_expiry_without_scheduler(db):
    s = create_signal(db, 1, symbol="NSE:TEST-EQ", strategy="trend", source_key="a",
                      reference_price=100, ttl_minutes=30)
    assert effective_status(s, s.created_at+timedelta(minutes=31)) == "EXPIRED"
    assert list_signals(db, 2) == []
    assert len(list_signals(db, 1)) == 1
    assert "NSE:TEST-EQ" in daily_digest(db, 1, "midday")

def test_transitions_and_duplicate(db):
    s = create_signal(db, 1, symbol="NSE:TEST-EQ", strategy="trend", source_key="a",
                      reference_price=100, ttl_minutes=30)
    transition(db,s,"ACTIVE","trend confirmed")
    with pytest.raises(HTTPException):
        transition(db,s,"WATCHING","should fail")
    with pytest.raises(HTTPException):
        create_signal(db, 1, symbol="NSE:TEST-EQ", strategy="trend", source_key="a",
                      reference_price=100, ttl_minutes=30)

def test_paper_account_and_fill_guards(db):
    a = create_paper_account(db, 1, 10000)
    s = create_signal(db, 1, symbol="NSE:TEST-EQ", strategy="trend", source_key="x",
                      reference_price=100, ttl_minutes=30)
    transition(db,s,"ACTIVE","valid pattern")
    with pytest.raises(HTTPException):
        paper_fill(db, 1, s.id, 1, 100, s.created_at)
    assert create_paper_account(db, 1, 5000).cash == 10000
    f=paper_fill(db, 1, s.id, 10, 100, s.created_at+timedelta(seconds=1), 2)
    assert f.quantity == 10
    assert db.exec(select(PaperTrade)).one().user_id == 1
    with pytest.raises(HTTPException):
        paper_fill(db, 1, s.id, 10, 100, s.created_at+timedelta(seconds=2), 2)
    assert db.exec(select(__import__("intelligence.models",fromlist=["PaperAccount"]).PaperAccount)).one().cash == 8998

def test_safety_and_cross_account(db):
    create_paper_account(db, 1, 50)
    s=create_signal(db, 1, symbol="NSE:TEST-EQ", strategy="trend", source_key="x",
                    reference_price=100, ttl_minutes=30)
    transition(db,s,"ACTIVE","valid pattern")
    with pytest.raises(HTTPException):
        paper_fill(db, 2, s.id, 1, 100, s.created_at+timedelta(seconds=1))
    with pytest.raises(HTTPException):
        paper_fill(db, 1, s.id, 1, 100, s.created_at+timedelta(seconds=1))
