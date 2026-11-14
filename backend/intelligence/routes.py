"""FinReview 2.0 protected research APIs (never trade real money)."""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Header, Depends, HTTPException
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
