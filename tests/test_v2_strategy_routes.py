import sys
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from models.portable_models import User
from intelligence.candle_cache import CachedCandle
from intelligence.routes import analyze_cached

@pytest.fixture
def session():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s

def test_cached_research_without_data(session):
    result=analyze_cached("csv","TEST","breakout",user=SimpleNamespace(id=2),session=session)
    assert result["status"]=="NO_DATA"
    assert result["analysis"] is None
    assert result["execution_mode"]=="RESEARCH_ONLY"

def test_research_rejects_unknown_provider(session):
    with pytest.raises(HTTPException) as exc:
        analyze_cached("invalid","TEST","breakout",user=SimpleNamespace(id=1),session=session)
    assert exc.value.status_code==422
