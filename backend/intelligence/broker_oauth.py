"""Opt-in, server-side broker authorization-code exchange groundwork.

No customer passwords/PINs handled; OAuth initiation requires FinReview login.
Callbacks are bound to an unpredictable, single-use state stored as a hash.
All client secrets are server-side environment values. No trading methods.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode
import httpx
from sqlalchemy import UniqueConstraint, update
from sqlmodel import Field, SQLModel, Session, select

from .credential_vault import BROKERS, store_trusted

class BrokerAuthError(ValueError):
    pass

class BrokerAuthAttempt(SQLModel, table=True):
    __tablename__ = "v2_broker_auth_attempts"
    __table_args__ = (UniqueConstraint("state_hash", name="uq_v2_broker_state"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    broker: str = Field(index=True, max_length=24)
    state_hash: str = Field(max_length=64)
    expires_at: datetime
    claimed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

def _now(now=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise BrokerAuthError("Timezone-aware clock required")
    return current.astimezone(timezone.utc)

def broker_settings(broker):
    if broker not in BROKERS:
        raise BrokerAuthError("Unsupported broker")
    prefix = {"fyers":"FYERS", "upstox":"UPSTOX", "zerodha":"ZERODHA"}[broker]
    client_id = os.getenv(prefix + "_CLIENT_ID" if broker != "zerodha" else "ZERODHA_API_KEY", "")
    secret = os.getenv(prefix + "_APP_SECRET" if broker == "fyers" else
                       prefix + "_CLIENT_SECRET" if broker == "upstox" else "ZERODHA_API_SECRET", "")
    redirect = os.getenv("FINREVIEW_" + prefix + "_REDIRECT_URI", "")
    if (not client_id or not secret or not redirect or not redirect.startswith("https://")
        or "#" in redirect or "?" in redirect or len(redirect)>512):
        raise BrokerAuthError("Broker OAuth app configuration missing or invalid")
    return client_id, secret, redirect

def begin(session: Session, user_id: int, broker: str, *, now=None):
    """Create a single-use state, return broker login URL, never return secrets."""
    if not isinstance(user_id,int) or user_id < 1:
        raise BrokerAuthError("Invalid account")
    client_id, _secret, redirect = broker_settings(broker)
    current=_now(now)
    # Cap outstanding attempts to limit abuse and uncontrolled DB growth.
    outstanding=session.exec(select(BrokerAuthAttempt).where(
        BrokerAuthAttempt.user_id==user_id,
        BrokerAuthAttempt.broker==broker,
        BrokerAuthAttempt.claimed_at.is_(None))).all()
    pending=0
    for attempt in outstanding:
        expiry=attempt.expires_at
        if expiry.tzinfo is None:
            expiry=expiry.replace(tzinfo=timezone.utc)
        if expiry>current:
            pending+=1
    if pending>=3:
        raise BrokerAuthError("Too many pending broker authorization attempts")
    state=secrets.token_urlsafe(32)
    session.add(BrokerAuthAttempt(user_id=user_id, broker=broker,
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                expires_at=current+timedelta(minutes=5), created_at=current))
    session.commit()
    if broker=="upstox":
        base="https://api.upstox.com/v2/login/authorization/dialog"
        args={"response_type":"code", "client_id":client_id,
              "redirect_uri":redirect, "state":state}
    elif broker=="fyers":
        base="https://api-t1.fyers.in/api/v3/generate-authcode"
        args={"response_type":"code","client_id":client_id,
              "redirect_uri":redirect,"state":state}
    else:
        base="https://kite.zerodha.com/connect/login"
        args={"v":"3","api_key":client_id,
              "redirect_params":"finreview_state="+state}
    return {"authorization_url":base+"?"+urlencode(args),
            "provider":broker,"expires_in_seconds":300,
            "status":"PENDING","no_real_orders":True}

def claim_state(session: Session, broker: str, state: str, *, now=None):
    """Single-use atomic claim; do not exchange expired/replayed/wrong-broker codes."""
    if broker not in BROKERS or not isinstance(state,str) or not (20 <= len(state) <= 200):
        raise BrokerAuthError("Invalid or expired authorization attempt")
    current=_now(now)
    digest=hashlib.sha256(state.encode()).hexdigest()
    row=session.exec(select(BrokerAuthAttempt).where(BrokerAuthAttempt.state_hash==digest)).first()
    if row is None or not hmac.compare_digest(row.broker,broker):
        raise BrokerAuthError("Invalid or expired authorization attempt")
    expires=row.expires_at
    if expires.tzinfo is None:  # SQLite stores naive timestamp
        expires=expires.replace(tzinfo=timezone.utc)
    if expires<=current:
        raise BrokerAuthError("Invalid or expired authorization attempt")
    changed=session.exec(update(BrokerAuthAttempt).where(
        BrokerAuthAttempt.id==row.id, BrokerAuthAttempt.claimed_at.is_(None)
    ).values(claimed_at=current))
    session.commit()
    if changed.rowcount != 1:
        raise BrokerAuthError("Authorization attempt already consumed")
    return row.user_id

async def exchange_code(broker: str, code: str, *, client=None):
    """Server-to-server token exchange; never return raw response or exception."""
    if broker not in BROKERS or not isinstance(code,str) or not (1<=len(code)<=2048):
        raise BrokerAuthError("Invalid authorization code")
    app_id, secret, redirect=broker_settings(broker)
    if broker=="upstox":
        url="https://api.upstox.com/v2/login/authorization/token"
        kwargs={"data":{"code":code,"client_id":app_id,"client_secret":secret,
                        "redirect_uri":redirect,"grant_type":"authorization_code"}}
    elif broker=="fyers":
        url="https://api-t1.fyers.in/api/v3/validate-authcode"
        app_hash=hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()
        kwargs={"json":{"grant_type":"authorization_code","appIdHash":app_hash,"code":code}}
    else:
        url="https://api.kite.trade/session/token"
        checksum=hashlib.sha256((app_id+code+secret).encode()).hexdigest()
        kwargs={"data":{"api_key":app_id,"request_token":code,"checksum":checksum},
                "headers":{"X-Kite-Version":"3"}}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=12, follow_redirects=False) as http:
                res=await http.post(url, **kwargs)
        else:
            res=await client.post(url, **kwargs)
        res.raise_for_status()
        payload=res.json()
        if broker=="fyers":
            token=payload.get("access_token") if payload.get("s")=="ok" else None
        elif broker=="upstox":
            token=payload.get("access_token")
        else:
            token=payload.get("data",{}).get("access_token") if payload.get("status")=="success" else None
        if not isinstance(token,str) or not token:
            raise BrokerAuthError("Broker rejected authorization code")
        return {"access_token":token, "app_id":app_id}
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise BrokerAuthError("Broker authorization exchange failed") from None

async def complete(session: Session, broker: str, state: str, code: str, *, client=None, now=None):
    """Consume state BEFORE network exchange. Failed exchanges cannot be replayed."""
    current=_now(now)
    user_id=claim_state(session, broker, state, now=current)
    credentials=await exchange_code(broker, code, client=client)
    # Use a conservative local upper bound, never a promise of token validity.
    from .broker_token_expiry import conservative_expiry
    expiry=conservative_expiry(broker,current)
    for purpose in ("historical", "quotes"):
        store_trusted(session,user_id,broker,purpose,credentials,expires_at=expiry)
    return {"provider":broker,"state":"STORED_UNVERIFIED",
            "live_validation":"NOT_PERFORMED","trading_enabled":False}
