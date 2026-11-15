"""Non-secret, per-user broker selection. Credentials are NOT stored here."""
from typing import Optional
from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, Session, select
from .market_data import MarketDataError

CHOICES = ("fyers", "upstox", "zerodha")

class BrokerPreference(SQLModel, table=True):
    __tablename__ = "v2_broker_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_v2_broker_preference_user"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    historical_provider: str = Field(max_length=20)
    quotes_provider: str = Field(max_length=20)

def choose_brokers(session: Session, user_id: int, history: str, quotes: str):
    if history not in CHOICES or quotes not in CHOICES:
        raise MarketDataError("Choose FYERS, Upstox or Zerodha")
    pref = session.exec(select(BrokerPreference).where(BrokerPreference.user_id == user_id)).first()
    if pref is None:
        pref = BrokerPreference(user_id=user_id, historical_provider=history, quotes_provider=quotes)
    else:
        pref.historical_provider, pref.quotes_provider = history, quotes
    session.add(pref)
    session.commit()
    session.refresh(pref)
    return pref

def selected_brokers(session: Session, user_id: int):
    pref = session.exec(select(BrokerPreference).where(BrokerPreference.user_id == user_id)).first()
    return {"historical_provider": pref.historical_provider if pref else None,
            "quotes_provider": pref.quotes_provider if pref else None,
            "available_choices": list(CHOICES),
            "credentials_storage": "NOT_IMPLEMENTED"}
