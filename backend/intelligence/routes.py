"""FinReview 2.0 protected research APIs (never trade real money)."""
from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, Header, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlmodel import Session, select
from database import get_session
from models.portable_models import User
from .models import StrategySignal, PaperAccount, PaperTrade
from .service import (
    create_signal, list_signals, get_signal, transition, create_paper_account
)

router = APIRouter(prefix="/v2", tags=["FinReview 2.0 research"])

def current_user(authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    # Imported lazily to avoid import cycle with main.py during router startup.
    from main import _verify_token
    return _verify_token(authorization, session)

class NewSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=64)
    strategy: str = Field(min_length=1, max_length=80)
    source_key: str = Field(min_length=1, max_length=160)
    reference_price: float = Field(gt=0, allow_inf_nan=False)
    ttl_minutes: int = Field(ge=1, le=4320)
    rationale: str = Field(default="", max_length=1600)
    entry_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    target_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    invalidation_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)

class ChangeSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    reason: str = Field(min_length=3, max_length=400)

class PaperInit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_cash: float = Field(default=100000, gt=0, le=100000000, allow_inf_nan=False)

@router.get("/signals")
def signals(active_only: bool = True, user: User = Depends(current_user),
            session: Session = Depends(get_session)):
    return list_signals(session, user.id, active_only)

@router.get("/signals/{signal_id}")
def signal_details(signal_id: int, user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    signal = get_signal(session, user.id, signal_id)
    from .service import effective_status
    return {**signal.model_dump(), "effective_status": effective_status(signal)}

# Create/transition endpoints require explicit server-side research authorization;
# this prevents normal browser users from manufacturing profitable signals.
def research_admin(authorization: Optional[str] = Header(None),
                   session: Session = Depends(get_session)):
    import os, hmac
    user = current_user(authorization, session)
    expected = os.getenv("FINREVIEW_V2_RESEARCH_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(403, "Research writer disabled")
    # This header is separate from browser login and is only for internal jobs.
    return user

@router.post("/paper-account")
def open_paper_account(req: PaperInit, user: User = Depends(current_user),
                       session: Session = Depends(get_session)):
    return create_paper_account(session, user.id, req.initial_cash)

@router.get("/paper-account")
def get_paper_account(user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    a = session.exec(select(PaperAccount).where(PaperAccount.user_id == user.id)).first()
    if not a:
        return {"initialized": False, "cash": None, "trades": []}
    trades = session.exec(select(PaperTrade).where(PaperTrade.user_id == user.id)
                          .order_by(PaperTrade.filled_at.desc())).all()
    return {"initialized": True, "account": a, "trades": trades, "execution_mode": "PAPER_ONLY"}

@router.get("/status")
def v2_status(user: User = Depends(current_user)):
    return {"version": "2.0-development", "mode": "RESEARCH_ONLY",
            "fyers_trading": False, "real_orders": False}


# Historical data is disabled on public Community deployments by default.
# A shared server-side provider token must NEVER be exposed as an open data proxy.
@router.get("/market/providers")
def market_providers(user: User = Depends(current_user)):
    from .providers import available_providers
    import os
    return {"available": available_providers(),
            "historical_api_enabled": os.getenv("FINREVIEW_V2_MARKET_API_ENABLED", "false").lower() == "true",
            "execution_mode": "READ_ONLY"}

@router.get("/market/history")
async def historical_market_data(provider: str, instrument: str, start: date,
                                 end: date, user: User = Depends(current_user)):
    import os
    from .providers import load_history
    from .market_data import MarketDataError
    if os.getenv("FINREVIEW_V2_MARKET_API_ENABLED", "false").lower() != "true":
        raise HTTPException(403, "Market-data API is disabled; enable only in a private licensed deployment")
    if (end - start).days > 365 or end < start:
        raise HTTPException(422, "Historical API requests limited to one year; use an offline importer for longer datasets")
    try:
        dataset = await load_history(provider, instrument, start, end)
        return dataset.as_json()
    except MarketDataError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        # Never leak broker tokens, provider URLs, or upstream HTTP error payloads.
        raise HTTPException(503, "Market-data provider unavailable")

@router.post("/market/csv/validate")
async def validate_csv_upload(request: Request, user: User = Depends(current_user)):
    from .market_data import parse_csv_history, MarketDataError
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 3_000_000:
            raise HTTPException(413, "CSV exceeds 3 MB")
        chunks.append(chunk)
    try:
        bars = parse_csv_history(b"".join(chunks))
    except MarketDataError as exc:
        raise HTTPException(422, str(exc))
    return {"valid": True, "rows": len(bars),
            "first_timestamp": bars[0].timestamp if bars else None,
            "last_timestamp": bars[-1].timestamp if bars else None,
            "stored": False, "adjustment": "unverified"}
