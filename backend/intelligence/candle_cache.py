"""Account-isolated, source-attributed research candle cache.

No cross-account data sharing. No market-data fetching or trading is performed here.
Cache rows are immutable by default: conflicting provider revisions are rejected.
"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, Session, select
from .market_data import Candle, MarketDataError, validate_candles

class CachedCandle(SQLModel, table=True):
    __tablename__ = "v2_cached_candles"
    __table_args__ = (UniqueConstraint("user_id", "provider", "instrument", "interval", "timestamp",
                                      name="uq_v2_candle_identity"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    provider: str = Field(index=True, max_length=24)
    instrument: str = Field(index=True, max_length=96)
    interval: str = Field(default="1d", max_length=12)
    timestamp: datetime = Field(index=True)
    open: float
    high: float
    low: float
    close: float
    volume: float
    adjustment: str = Field(default="provider_raw_unverified", max_length=40)
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

def save_candles(session: Session, user_id: int, provider: str, instrument: str,
                 candles: list[Candle], *, adjustment: str = "provider_raw_unverified"):
    """Atomically insert a validated batch; fail rather than overwrite revisions."""
    if not provider or not instrument or len(provider) > 24 or len(instrument) > 96:
        raise MarketDataError("Invalid provider or instrument")
    checked = validate_candles([c.as_json() for c in candles])
    if not checked:
        return {"inserted": 0, "unchanged": 0}
    # One query for all existing candles in this date span. No cross-user sharing.
    existing = session.exec(select(CachedCandle).where(
        CachedCandle.user_id == user_id,
        CachedCandle.provider == provider,
        CachedCandle.instrument == instrument,
        CachedCandle.interval == "1d",
        CachedCandle.timestamp >= checked[0].timestamp,
        CachedCandle.timestamp <= checked[-1].timestamp)).all()
    by_timestamp = {c.timestamp.astimezone(timezone.utc): c for c in existing}
    new_rows = []
    unchanged = 0
    for bar in checked:
        previous = by_timestamp.get(bar.timestamp)
        if previous is not None:
            if (previous.open, previous.high, previous.low, previous.close, previous.volume,
                previous.adjustment) != (bar.open, bar.high, bar.low, bar.close, bar.volume, adjustment):
                raise MarketDataError("Provider revised a cached candle; manual reconciliation required")
            unchanged += 1
        else:
            new_rows.append(CachedCandle(
                user_id=user_id, provider=provider, instrument=instrument,
                timestamp=bar.timestamp, open=bar.open, high=bar.high,
                low=bar.low, close=bar.close, volume=bar.volume, adjustment=adjustment))
    session.add_all(new_rows)
    session.commit()
    return {"inserted": len(new_rows), "unchanged": unchanged}

def read_candles(session: Session, user_id: int, provider: str, instrument: str,
                 start: datetime, end: datetime):
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise MarketDataError("Explicit UTC-aware query range required")
    rows = session.exec(select(CachedCandle).where(
        CachedCandle.user_id == user_id,
        CachedCandle.provider == provider,
        CachedCandle.instrument == instrument,
        CachedCandle.interval == "1d",
        CachedCandle.timestamp >= start,
        CachedCandle.timestamp <= end).order_by(CachedCandle.timestamp)).all()
    bars = tuple(Candle(r.timestamp.astimezone(timezone.utc), r.open, r.high, r.low, r.close, r.volume)
                 for r in rows)
    return {"provider": provider, "instrument": instrument, "interval": "1d",
            "count": len(bars), "bars": bars,
            "coverage": "unverified_partial", "adjustment": "provider_raw_unverified"}
