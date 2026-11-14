"""Deterministic, read-only signal lifecycle and account-isolated PAPER trading."""
from datetime import timedelta
from typing import Optional
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from .models import StrategySignal, SignalEvent, PaperAccount, PaperTrade, utc_now

VALID = {"WATCHING", "ACTIVE", "TRIGGERED", "EXPIRED", "INVALIDATED"}
ALLOWED = {
    "WATCHING": {"ACTIVE", "INVALIDATED", "EXPIRED"},
    "ACTIVE": {"TRIGGERED", "INVALIDATED", "EXPIRED"},
    "TRIGGERED": {"EXPIRED", "INVALIDATED"},
    "EXPIRED": set(), "INVALIDATED": set()
}

def effective_status(signal: StrategySignal, now=None) -> str:
    now = now or utc_now()
    if signal.status in ("INVALIDATED", "EXPIRED"):
        return signal.status
    if now >= signal.expires_at:
        return "EXPIRED"
    return signal.status

def transition(session: Session, signal: StrategySignal, target: str, reason: str, now=None):
    now = now or utc_now()
    current = effective_status(signal, now)
    if target not in VALID or target not in ALLOWED.get(current, set()):
        raise HTTPException(409, f"Invalid transition {current} -> {target}")
    previous = signal.status
    signal.status = target
    if target == "INVALIDATED":
        signal.invalidated_at = now
    session.add(signal)
    session.add(SignalEvent(user_id=signal.user_id, signal_id=signal.id,
                            old_status=previous, new_status=target, reason=reason, created_at=now))
    session.commit()
    session.refresh(signal)
    return signal

def create_signal(session: Session, user_id: int, *, symbol: str, strategy: str,
                  source_key: str, reference_price: float, ttl_minutes: int,
                  rationale: str = "", data_timestamp=None, entry_price=None,
                  invalidation_price=None, target_price=None, *, now=None):
    # Clock injection is internal only, for deterministic simulation/replay tests.
    now = now or utc_now()
    if not symbol or not strategy or not source_key or ttl_minutes < 1 or ttl_minutes > 4320:
        raise HTTPException(422, "Invalid signal data")
    if reference_price <= 0 or not __import__("math").isfinite(reference_price):
        raise HTTPException(422, "Invalid price")
    for value in (entry_price, invalidation_price, target_price):
        if value is not None and (value <= 0 or not __import__("math").isfinite(value)):
            raise HTTPException(422, "Invalid price")
    data_timestamp = data_timestamp or now
    if abs((now - data_timestamp).total_seconds()) > 900:
        raise HTTPException(422, "Source data is stale or in the future")
    obj = StrategySignal(user_id=user_id, symbol=symbol.upper(), strategy=strategy,
        source_key=source_key, reference_price=reference_price, status="WATCHING",
        entry_price=entry_price, invalidation_price=invalidation_price, target_price=target_price,
        data_timestamp=data_timestamp, rationale=rationale,
        created_at=now, expires_at=now + timedelta(minutes=ttl_minutes))
    session.add(obj)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Duplicate signal source_key")
    session.refresh(obj)
    return obj

def list_signals(session: Session, user_id: int, active_only: bool = True):
    signals = session.exec(select(StrategySignal).where(StrategySignal.user_id == user_id)
                           .order_by(StrategySignal.created_at.desc())).all()
    return [{**s.model_dump(), "effective_status": effective_status(s)}
            for s in signals if not active_only or effective_status(s) in ("WATCHING", "ACTIVE")]

def get_signal(session: Session, user_id: int, signal_id: int):
    obj = session.exec(select(StrategySignal).where(
        StrategySignal.id == signal_id, StrategySignal.user_id == user_id)).first()
    if obj is None:
        raise HTTPException(404, "Signal not found")
    return obj

def create_paper_account(session: Session, user_id: int, initial_cash: float = 100000):
    if not 0 < initial_cash <= 100000000:
        raise HTTPException(422, "Invalid virtual balance")
    existing = session.exec(select(PaperAccount).where(PaperAccount.user_id == user_id)).first()
    if existing:
        return existing
    account = PaperAccount(user_id=user_id, cash=initial_cash, initial_cash=initial_cash)
    session.add(account)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return session.exec(select(PaperAccount).where(PaperAccount.user_id == user_id)).one()
    session.refresh(account)
    return account

def paper_fill(session: Session, user_id: int, signal_id: int, qty: int,
               observed_price: float, observed_at, fees: float = 0):
    """A simulated fill requires an observation AFTER creation and no later than expiry.

    Never submits broker orders. Caller supplies a validated recorded quote/candle;
    untrusted browser clients MUST NOT be permitted to invoke this function.
    """
    import math
    signal = get_signal(session, user_id, signal_id)
    if signal.side != "BUY" or signal.status not in ("ACTIVE", "TRIGGERED"):
        raise HTTPException(409, "Not a long-entry candidate")
    if (observed_at <= signal.created_at or observed_at > signal.expires_at
        or signal.invalidated_at is not None or not math.isfinite(observed_price)
        or observed_price <= 0 or qty <= 0 or not math.isfinite(fees) or fees < 0):
        raise HTTPException(409, "Invalid simulated fill")
    account = session.exec(select(PaperAccount).where(PaperAccount.user_id == user_id).with_for_update()).first()
    if not account:
        raise HTTPException(409, "Paper account not initialized")
    existing = session.exec(select(PaperTrade).where(PaperTrade.user_id == user_id,
                                                     PaperTrade.signal_id == signal_id)).first()
    if existing:
        raise HTTPException(409, "Signal already filled")
    cost = observed_price * qty + fees
    if cost > account.cash:
        raise HTTPException(409, "Insufficient virtual cash")
    account.cash -= cost
    trade = PaperTrade(user_id=user_id, signal_id=signal.id, symbol=signal.symbol,
                       quantity=qty, fill_price=observed_price, fees=fees, filled_at=observed_at)
    session.add(account)
    session.add(trade)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Duplicate paper trade")
    session.refresh(trade)
    return trade
