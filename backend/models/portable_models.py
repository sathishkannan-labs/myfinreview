from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship
from enum import Enum
from sqlalchemy import CheckConstraint, UniqueConstraint, DateTime

class InsightType(str, Enum):
    CHANGE_EXPLANATION = "CHANGE_EXPLANATION"
    PORTFOLIO_BRIEFING = "PORTFOLIO_BRIEFING"
    RISK_ALERT = "RISK_ALERT"

class TransactionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    external_id: str = Field(index=True, unique=True, default_factory=lambda: str(datetime.now().timestamp())) 
    email: str = Field(index=True, unique=True)
    password: str 
    full_name: Optional[str] = None
    dob_year: Optional[int] = None
    city: Optional[str] = None
    state: Optional[str] = None
    target_allocation: Optional[str] = None # JSON string of symbol: percentage
    drift_sensitivity: float = Field(default=5.0) # Percentage threshold for drift alerts
    last_intelligence_refresh: Optional[datetime] = Field(default=None, sa_type=DateTime)
    created_at: datetime = Field(default_factory=datetime.now, sa_type=DateTime)

class Holding(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint('user_id', 'symbol', name='uq_holding_user_symbol'),
        CheckConstraint('quantity >= 0', name='ck_holding_quantity_nonnegative'),
        CheckConstraint('avg_price >= 0', name='ck_holding_price_nonnegative'),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    symbol: str = Field(index=True)
    quantity: float
    avg_price: float
    asset_type: str # 'STOCK' or 'MF'
    last_updated: datetime = Field(default_factory=datetime.now, sa_type=DateTime)

class Transaction(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint('quantity > 0', name='ck_transaction_quantity_positive'),
        CheckConstraint('price > 0', name='ck_transaction_price_positive'),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    symbol: str
    type: TransactionType = Field(index=True)
    quantity: float
    price: float
    date: datetime = Field(default_factory=datetime.now, sa_type=DateTime)
    hash: Optional[str] = Field(default=None, index=True, unique=True) # Composite hash for upsert

class Insight(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    type: InsightType
    content: str
    importance_score: float = 5.0
    change_hash: str
    timestamp: datetime = Field(default_factory=datetime.now, sa_type=DateTime)

class AlertRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    type: str 
    symbol: Optional[str] = None
    threshold: float = 0.0
    target_value: Optional[float] = None
    is_enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.now, sa_type=DateTime)

class AlertEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    rule_id: Optional[int] = Field(default=None, foreign_key="alertrule.id", ondelete="SET NULL")
    type: str
    message: str
    severity: str 
    timestamp: datetime = Field(default_factory=datetime.now, sa_type=DateTime)
    data_json: Optional[str] = None 

class NewsArticle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    title: str
    link: str
    category: str = Field(default="NEWS", index=True) # e.g., NEWS, ANNOUNCEMENT, RESULT
    summary: Optional[str] = None
    published_at: datetime = Field(default_factory=datetime.now, sa_type=DateTime)
    sentiment: Optional[float] = None

class NewsHash(SQLModel, table=True):
    hash: str = Field(primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, sa_type=DateTime)

class AnalyticsSummary(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    total_valuation: float
    total_cost: float
    xirr: float = 0.0
    concentration_score: float = 0.0
    is_concentrated: bool = False
    data_json: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now, sa_type=DateTime)

class PriceCache(SQLModel, table=True):
    symbol: str = Field(primary_key=True)
    name: Optional[str] = None
    price: float
    timestamp: datetime = Field(default_factory=datetime.now, sa_type=DateTime)
