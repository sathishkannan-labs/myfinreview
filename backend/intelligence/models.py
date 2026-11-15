"""Version 2 additive database models; all monetary values are for PAPER trading only."""
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint, CheckConstraint

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class StrategySignal(SQLModel, table=True):
    __tablename__ = "strategy_signals"
    __table_args__ = (UniqueConstraint("user_id", "source_key", name="uq_v2_signal_source"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    symbol: str = Field(index=True, max_length=64)
    strategy: str = Field(max_length=80)
    source_key: str = Field(max_length=160)
    side: str = Field(default="BUY")
    status: str = Field(default="WATCHING", index=True)
    reference_price: float = Field(gt=0)
    entry_price: Optional[float] = None
    invalidation_price: Optional[float] = None
    target_price: Optional[float] = None
    rationale: str = Field(default="")
    data_timestamp: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime = Field(index=True)
    invalidated_at: Optional[datetime] = None

class SignalEvent(SQLModel, table=True):
    __tablename__ = "signal_events"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    signal_id: int = Field(foreign_key="strategy_signals.id", index=True)
    old_status: str
    new_status: str
    reason: str
    created_at: datetime = Field(default_factory=utc_now)

class PaperAccount(SQLModel, table=True):
    __tablename__ = "paper_accounts"
    __table_args__ = (UniqueConstraint("user_id", name="uq_v2_paper_user"), CheckConstraint("cash >= 0", name="ck_v2_paper_cash"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    initial_cash: float = Field(gt=0)
    cash: float = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)

class PaperTrade(SQLModel, table=True):
    __tablename__ = "paper_trades"
    __table_args__ = (UniqueConstraint("user_id", "signal_id", name="uq_v2_trade_per_signal"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    signal_id: int = Field(foreign_key="strategy_signals.id", index=True)
    symbol: str
    side: str = Field(default="BUY")
    quantity: int = Field(gt=0)
    fill_price: float = Field(gt=0)
    fees: float = Field(ge=0)
    filled_at: datetime = Field(default_factory=utc_now)
    status: str = Field(default="OPEN")
    exit_price: Optional[float] = None
    exit_fees: float = Field(default=0)
    closed_at: Optional[datetime] = None

class NotificationDelivery(SQLModel, table=True):
    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key", name="uq_v2_notification_dedupe"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    dedupe_key: str = Field(max_length=200)
    status: str = Field(default="PENDING")
    created_at: datetime = Field(default_factory=utc_now)
    delivered_at: Optional[datetime] = None
    error: Optional[str] = None
