"""Per-account encrypted broker credential vault; no public token write endpoint.

Intended for trusted, provider-approved OAuth callbacks and server-side operators.
Secrets are encrypted at rest via Fernet; key must be kept in a separate secret
manager. Never log tokens, include them in API output or expose to frontend JS.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel, Session, select

BROKERS = ("fyers", "upstox", "zerodha")
KINDS = ("historical", "quotes")


class BrokerConnection(SQLModel, table=True):
    __tablename__ = "v2_broker_connections"
    __table_args__ = (UniqueConstraint("user_id", "provider", "purpose",
                                      name="uq_v2_broker_connection_scope"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    provider: str = Field(index=True, max_length=24)
    purpose: str = Field(max_length=24)
    encrypted_credentials: str
    expires_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _cipher():
    value = os.getenv("FINREVIEW_BROKER_VAULT_KEY", "").strip()
    if not value:
        raise RuntimeError("Broker credential vault is disabled")
    try:
        return Fernet(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise RuntimeError("Invalid broker vault key configuration") from None


def _scope(user_id, provider, purpose):
    if not isinstance(user_id, int) or user_id <= 0 or provider not in BROKERS or purpose not in KINDS:
        raise ValueError("Invalid broker connection scope")


def _utc_expiry(expiry):
    if expiry is None:
        return None
    return expiry.replace(tzinfo=timezone.utc) if expiry.tzinfo is None else expiry.astimezone(timezone.utc)


def status(session: Session, user_id: int):
    """Return safe metadata only, never encrypted blobs or token suffixes."""
    rows = session.exec(select(BrokerConnection).where(
        BrokerConnection.user_id == user_id)).all()
    now = datetime.now(timezone.utc)
    connections = []
    for row in rows:
        expiry = _utc_expiry(row.expires_at)
        connections.append({"provider": row.provider, "purpose": row.purpose,
                            "state": "EXPIRED" if expiry and expiry <= now else "STORED_UNVERIFIED",
                            "expires_at": expiry.isoformat() if expiry else None})
    return {"connections": connections,
            "oauth_configured": False, "live_validation": "NOT_PERFORMED"}


def store_trusted(session: Session, user_id: int, provider: str, purpose: str,
                  credentials: dict, *, expires_at: datetime | None = None, commit: bool = True):
    """Trusted server-side integration only. No raw credentials via browser API."""
    _scope(user_id, provider, purpose)
    if not isinstance(credentials, dict) or not credentials or any(
        not isinstance(k, str) or not isinstance(v, str) or not k or not v
        for k, v in credentials.items()
    ):
        raise ValueError("Invalid broker credential payload")
    if expires_at is not None and (expires_at.tzinfo is None or expires_at.utcoffset() is None):
        raise ValueError("Expiry requires explicit timezone")
    payload = {"user_id": user_id, "provider": provider, "purpose": purpose,
               "credentials": credentials}
    token = _cipher().encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode("ascii")
    row = session.exec(select(BrokerConnection).where(
        BrokerConnection.user_id == user_id, BrokerConnection.provider == provider,
        BrokerConnection.purpose == purpose)).first()
    if row is None:
        row = BrokerConnection(user_id=user_id, provider=provider, purpose=purpose,
                               encrypted_credentials=token)
    else:
        row.encrypted_credentials = token
    row.expires_at = expires_at
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    if commit:
        session.commit()
    return {"provider": provider, "purpose": purpose, "state": "STORED_UNVERIFIED"}


def read_trusted(session: Session, user_id: int, provider: str, purpose: str):
    """Only trusted server-side code may decrypt; reject wrong user/provider."""
    _scope(user_id, provider, purpose)
    row = session.exec(select(BrokerConnection).where(
        BrokerConnection.user_id == user_id,
        BrokerConnection.provider == provider,
        BrokerConnection.purpose == purpose)).first()
    if row is None:
        raise ValueError("Broker connection not configured")
    if row.expires_at is not None:
        if _utc_expiry(row.expires_at) <= datetime.now(timezone.utc):
            raise ValueError("Broker session expired")
    try:
        data = json.loads(_cipher().decrypt(row.encrypted_credentials.encode("ascii")))
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("Broker credentials unavailable") from None
    if any(data.get(k) != expected for k, expected in (
        ("user_id", user_id), ("provider", provider), ("purpose", purpose)
    )):
        raise RuntimeError("Broker credential scope mismatch")
    return data["credentials"]


def disconnect(session: Session, user_id: int, provider: str):
    """Remove all local tokens for this user's broker; revoke upstream separately."""
    if provider not in BROKERS or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("Invalid broker connection scope")
    rows = session.exec(select(BrokerConnection).where(
        BrokerConnection.user_id == user_id, BrokerConnection.provider == provider)).all()
    for row in rows:
        session.delete(row)
    session.commit()
    return {"provider": provider, "local_credentials_deleted": True,
            "upstream_revocation": "NOT_PERFORMED"}
