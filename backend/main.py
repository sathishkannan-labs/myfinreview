from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Literal
from datetime import datetime, timedelta, timezone
from database import get_session, init_db
from models.portable_models import User, Holding, Transaction, Insight, NewsArticle, InsightType, AlertRule, AlertEvent, AnalyticsSummary, PriceCache
from mangum import Mangum # For AWS Lambda support
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field, field_validator, ConfigDict
import uvicorn
import json
import csv
import os

from services.portable_portfolio_service import PortablePortfolioService
from services.portable_price_service import PortablePriceService
from services.reference_data_service import ReferenceDataService
from ai.portable_insight_service import PortableInsightService
from alerts.portable_alerts_service import PortableAlertsService
from news.portable_news_service import PortableNewsService
from analytics.portable_analytics_service import PortableAnalyticsService
from services.cas_service import CASService
from services.overlap_service import OverlapService
from config import settings
import bcrypt
import logging
import asyncio
import hashlib
import hmac
import time

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize Database with retry
    logger.info("Application starting up...")
    # Refuse known/default secrets before opening the hosted API.
    _auth_secret()
    for i in range(3):
        try:
            logger.info(f"Connecting to DB (Attempt {i+1}/3)...")
            init_db()
            
            # --- MIGRATION: Fix Foreign Key for Alert Rule Deletion ---
            from sqlalchemy import text, inspect
            from database import engine, is_sqlite
            with Session(engine) as session:
                try:
                    # SQLite doesn't support ALTER COLUMN or DROP CONSTRAINT or IF EXISTS in ADD COLUMN
                    if is_sqlite:
                        # For SQLite, we can only add columns if they don't exist
                        # Note: SQLite doesn't support 'ADD COLUMN IF NOT EXISTS' directly in older versions
                        # We use PRAGMA to check for existence
                        inspector = inspect(engine)
                        
                        # Fix NewsArticle category
                        columns = [c['name'] for c in inspector.get_columns('newsarticle')]
                        if 'category' not in columns:
                            session.execute(text("ALTER TABLE newsarticle ADD COLUMN category VARCHAR DEFAULT 'NEWS'"))
                        
                        # AlertEvent rule_id nullable: SQLite doesn't support ALTER COLUMN.
                        # If we really need to change constraints, we'd need to recreate the table.
                        # For now, we just skip it or log it.
                        logger.info("SQLite: Skipping complex migrations (ALTER COLUMN / DROP CONSTRAINT)")
                    else:
                        # PostgreSQL / Other
                        session.execute(text("ALTER TABLE alertevent ALTER COLUMN rule_id DROP NOT NULL;"))
                        session.execute(text("ALTER TABLE newsarticle ADD COLUMN IF NOT EXISTS category VARCHAR DEFAULT 'NEWS';"))
                        session.execute(text("ALTER TABLE analyticssummary ADD COLUMN IF NOT EXISTS data_json VARCHAR;"))
                        session.execute(text("""
                            ALTER TABLE alertevent 
                            DROP CONSTRAINT IF EXISTS alertevent_rule_id_fkey,
                            ADD CONSTRAINT alertevent_rule_id_fkey 
                            FOREIGN KEY (rule_id) REFERENCES alertrule(id) ON DELETE SET NULL;
                        """))
                    
                    session.commit()
                    logger.info("Database migration: news categories updated.")
                except Exception as me:
                    logger.warning(f"Migration skipped or failed: {me}")

            logger.info("Database Ready.")
            
            # Pre-load reference data into memory
            logger.info("Pre-loading reference data...")
            ReferenceDataService() 
            
            break
        except Exception as e:
            logger.warning(f"Database init failed: {e}")
            if i == 2: 
                logger.critical("Could not connect to DB after 3 attempts.")
            else:
                await asyncio.sleep(5) # Use async sleep to allow shutdown signals
    yield
    # Shutdown logic (optional)
    pass

app = FastAPI(title="FinReview API - Community Edition", lifespan=lifespan)

# --- CORS CONFIGURATION ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- REQUEST MODELS ---
class AuthRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    dob_year: Optional[int] = None
    city: Optional[str] = None
    state: Optional[str] = None

class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: Optional[str] = None
    dob_year: Optional[int] = None
    city: Optional[str] = None
    state: Optional[str] = None
    target_allocation: Optional[str] = None
    drift_sensitivity: float = 5.0

class ProfileUpdateResponse(BaseModel):
    message: str
    user: UserPublic

class TransactionRequest(BaseModel):
    user_id: int
    symbol: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9^][A-Z0-9._^&=-]*$")
    name: Optional[str] = Field(default=None, max_length=256)
    type: Literal['BUY', 'SELL']
    quantity: float = Field(gt=0, allow_inf_nan=False)
    price: float = Field(gt=0, allow_inf_nan=False)

    @field_validator('symbol', mode='before')
    @classmethod
    def normalize_symbol(cls, value):
        return value.strip().upper() if isinstance(value, str) else value
    date: Optional[datetime] = None

class AlertRuleRequest(BaseModel):
    user_id: int
    type: str
    symbol: Optional[str] = None
    threshold: float = 0.0
    target_value: Optional[float] = None

# --- AUTH ROUTES ---
def _auth_secret() -> bytes:
    secret = os.getenv("AUTH_SECRET_KEY") or os.getenv("SECRET_KEY", "")
    if len(secret.encode("utf-8")) < 32 or secret in {
        "finreview-local-dev-secret", "change-me-for-hosted-deployments"
    }:
        raise RuntimeError("Set AUTH_SECRET_KEY to a random secret of at least 32 bytes")
    return secret.encode("utf-8")


def _issue_token(user_id: int) -> str:
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    payload = f"{user_id}:{timestamp}"
    signature = hmac.new(_auth_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"frv1:{payload}:{signature}"


def _verify_token(authorization: Optional[str], session: Session) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    parts = token.split(":")
    if len(parts) != 4 or parts[0] != "frv1":
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    _, user_id_raw, timestamp, signature = parts
    payload = f"{user_id_raw}:{timestamp}"
    expected = hmac.new(_auth_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    try:
        user_id = int(user_id_raw)
        issued_at = datetime.fromtimestamp(int(timestamp), timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    now = datetime.now(timezone.utc)
    if issued_at > now + timedelta(seconds=60) or now - issued_at > timedelta(days=7):
        raise HTTPException(status_code=401, detail="Session expired")

    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Token user not found")
    return user


def _require_user_id(user_id: int, authorization: Optional[str], session: Session) -> User:
    user = _verify_token(authorization, session)
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Token does not match requested user")
    return user


def get_community_access_user(user_id: int, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    return _require_user_id(user_id, authorization, session)


def _auth_payload(user: User) -> dict:
    return {
        "token": _issue_token(user.id),
        "user_id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "dob_year": user.dob_year,
        "city": user.city,
        "state": user.state,
        "community_access": True,
        "target_allocation": user.target_allocation,
        "drift_sensitivity": getattr(user, "drift_sensitivity", 5.0),
        "last_intelligence_refresh": user.last_intelligence_refresh.isoformat() if user.last_intelligence_refresh else None
    }


async def load_sample_portfolio_for_user(user_id: int, session: Session) -> User:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing_tx = session.exec(select(Transaction).where(Transaction.user_id == user.id)).first()
    if existing_tx:
        return user

    service = PortablePortfolioService(session)
    price_service = PortablePriceService(session)
    ref_service = ReferenceDataService()
    sample_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sample_portfolio.csv"))
    sample_prices = {
        "RELIANCE.NS": 2950.0, "TCS.NS": 4100.0, "HDFCBANK.NS": 1715.0, "INFY.NS": 1625.0,
        "ICICIBANK.NS": 1090.0, "ITC.NS": 455.0, "RVNL.NS": 270.0, "120444": 48.2,
        "118989": 482.5, "BSE.NS": 2390.0, "BAJFINANCE.NS": 7050.0
    }
    with open(sample_path, newline="", encoding="utf-8") as sample_file:
        for row in csv.DictReader(sample_file):
            symbol = row["symbol"].strip().upper()
            name = ref_service.get_asset_name(symbol) or symbol
            service.add_transaction(user.id, symbol, row["type"].strip().upper(), float(row["quantity"]), float(row["price"]), datetime(2026, 2, 24), name)
            price_service.save_price(symbol, sample_prices.get(symbol, float(row["price"])), name=name)

    summary = await service.get_portfolio_summary(user.id)
    PortableAnalyticsService(session).calculate_and_save_summary(user.id, summary.get("total_valuation", 0), summary.get("holdings", []))
    session.add(Insight(
        user_id=user.id,
        type=InsightType.PORTFOLIO_BRIEFING,
        content="Sample briefing: This portfolio mixes large-cap equities, selected public-sector momentum exposure, and mutual funds so you can inspect allocation, risk, drift, and AI summaries. This content is informational only and should not be considered financial advice.",
        importance_score=8.5,
        change_hash=f"sample-briefing-{user.id}"
    ))
    user.last_intelligence_refresh = datetime.now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
@app.post("/auth/signup")
async def signup(request: AuthRequest, session: Session = Depends(get_session)):
    # Check if user exists
    existing = session.exec(select(User).where(User.email == request.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")
    
    user = User(
        email=request.email,
        password=bcrypt.hashpw(request.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8'), # Securely hashed
        full_name=request.full_name,
        dob_year=request.dob_year,
        city=request.city,
        state=request.state,
        external_id=str(datetime.now().timestamp())
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"message": "User created", "user_id": user.id}

@app.post("/auth/login")
async def login(request: AuthRequest, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == request.email)).first()
    if not user or not bcrypt.checkpw(request.password.encode('utf-8'), user.password.encode('utf-8')):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return _auth_payload(user)

@app.get("/auth/profile/{user_id}")
async def get_profile(user_id: int, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    user = _require_user_id(user_id, authorization, session)
    return {
        "user_id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "dob_year": user.dob_year,
        "city": user.city,
        "state": user.state,
        "community_access": True,
        "target_allocation": user.target_allocation,
        "drift_sensitivity": getattr(user, "drift_sensitivity", 5.0),
        "last_intelligence_refresh": user.last_intelligence_refresh.isoformat() if user.last_intelligence_refresh else None
    }

@app.post("/auth/profile", response_model=ProfileUpdateResponse)
async def update_profile(request: AuthRequest, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    user = _verify_token(authorization, session)
    
    user.full_name = request.full_name
    user.dob_year = request.dob_year
    user.city = request.city
    user.state = request.state # Added state persistency
    
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"message": "Profile updated", "user": user}

@app.post("/auth/forgot-password")
async def forgot_password(request: AuthRequest, session: Session = Depends(get_session)):
    raise HTTPException(status_code=501, detail="Password reset email is not enabled in v1.0.0. Please create a new demo account or reset credentials directly in your own deployment database.")




@app.post("/sample-portfolio/{user_id}")
async def load_sample_portfolio(user_id: int, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    _require_user_id(user_id, authorization, session)
    user = await load_sample_portfolio_for_user(user_id, session)
    return {"message": "Sample portfolio loaded", "user_id": user.id}
@app.get("/")
def read_root():
    return {"message": "Welcome to FinReview Portfolio Intelligence API (Community)"}

# --- PORTFOLIO ROUTES ---
@app.get("/portfolio/{user_id}/summary")
async def get_portfolio_summary(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except:
        return {"error": "Invalid user_id. Must be numeric."}
    _require_user_id(uid, authorization, session)
    service = PortablePortfolioService(session)
    summary = await service.get_portfolio_summary(uid)
    
    # Add refresh metadata
    user = session.get(User, uid)
    summary['last_intelligence_refresh'] = user.last_intelligence_refresh.isoformat() if user and user.last_intelligence_refresh else None
    summary['demo_access'] = True
    summary['target_allocation'] = user.target_allocation if user else None
    summary['drift_sensitivity'] = user.drift_sensitivity if user else 5.0
    
    return summary

@app.get("/portfolio/{user_id}", response_model=List[Holding])
async def get_holdings(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except:
        return []
    _require_user_id(uid, authorization, session)
    holdings = session.exec(select(Holding).where(Holding.user_id == uid)).all()
    return holdings

async def _refresh_after_transactions(uid: int, session: Session) -> bool:
    """Derived data can be retried without resubmitting a committed trade."""
    try:
        summary = await PortablePortfolioService(session).get_portfolio_summary(uid)
        PortableAnalyticsService(session).calculate_and_save_summary(
            uid, summary.get('total_valuation', 0), summary.get('holdings', []))
        PortableAlertsService(session).evaluate_all_rules(uid)
        return True
    except Exception:
        session.rollback()
        logger.exception("Transactions saved, derived refresh failed for user %s", uid)
        return False


def _record_transactions(uid: int, rows: list, session: Session):
    try:
        return PortablePortfolioService(session).add_transactions(
            uid, [tx.model_dump(exclude={'user_id'}) for tx in rows])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Transaction conflict; reload and retry") from exc


@app.post("/portfolio/transaction")
async def add_transaction(request: TransactionRequest, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    user = _require_user_id(request.user_id, authorization, session)
    uid = user.id
    holding = _record_transactions(uid, [request], session)[0]
    refreshed = await _refresh_after_transactions(uid, session)
    return {"message": "Transaction recorded", "holding": holding,
            "analytics_updated": refreshed}


@app.post("/portfolio/bulk-transaction")
async def add_bulk_transactions(request: List[TransactionRequest], authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    user = _verify_token(authorization, session)
    uid = user.id
    if any(tx.user_id != uid for tx in request):
        raise HTTPException(status_code=403, detail="All transactions must belong to the authenticated user")
    if not request:
        return {"message": "No transactions provided", "count": 0}
    if len(request) > 1000:
        raise HTTPException(status_code=400, detail="Import at most 1000 transactions per batch")
    # Provider validation happens before any trade is written. Name lookups do
    # not overwrite global market prices or acquire account locks over HTTP.
    price_service = PortablePriceService(session)
    ref_service = ReferenceDataService()
    for tx in request:
        if not await price_service.validate_symbol(tx.symbol):
            raise HTTPException(status_code=400, detail=f"Invalid symbol: {tx.symbol}")
        if not tx.name or tx.name == tx.symbol:
            tx.name = ref_service.get_asset_name(tx.symbol)
    _record_transactions(uid, request, session)
    refreshed = await _refresh_after_transactions(uid, session)
    return {"message": f"Recorded {len(request)} transactions", "count": len(request),
            "analytics_updated": refreshed}

# --- DEMO ANALYTICS ---
@app.post("/portfolio/upload-cas")
async def upload_cas(user_id: int, password: str, session: Session = Depends(get_session), user: User = Depends(get_community_access_user)):
    """CAS PDF parsing is intentionally disabled in Community Edition v1.0.0."""
    raise HTTPException(status_code=501, detail="CAS PDF parsing is not enabled in v1.0.0. Use CSV import or manual transaction entry.")

@app.get("/analytics/overlap/{user_id}")
async def get_mf_overlap(user_id: int, session: Session = Depends(get_session), user: User = Depends(get_community_access_user)):
    """
    Calculates commonality of stocks between different mutual funds.
    """
    overlap_service = OverlapService(session)
    return overlap_service.calculate_mf_overlap(user_id)

# --- ALERTS ROUTES ---
@app.post("/alerts/rule")
async def add_alert_rule(request: AlertRuleRequest, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    _require_user_id(request.user_id, authorization, session)
    logger.debug(f"Adding alert rule for {request.symbol} (User {request.user_id})")
    service = PortableAlertsService(session)
    
    if request.symbol:
        price_service = PortablePriceService(session)
        lp = await price_service.get_latest_price(request.symbol)
        logger.debug(f"Initial LTP fetch for {request.symbol} returned: {lp}")

    rule = service.add_rule(
        request.user_id,
        request.type,
        request.symbol,
        request.threshold,
        request.target_value
    )
    return {"message": "Alert rule added", "rule": rule}

@app.get("/alerts/{user_id}/rules", response_model=List[AlertRule])
def get_alert_rules(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except: return []
    _require_user_id(uid, authorization, session)
    service = PortableAlertsService(session)
    return service.get_rules(uid)

class BulkDeleteRequest(BaseModel):
    rule_ids: List[int]

@app.post("/alerts/rules/bulk-delete")
def bulk_delete_alert_rules(request: BulkDeleteRequest, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    if not request.rule_ids:
        return {"message": "No rules provided", "count": 0}

    user = _verify_token(authorization, session)
    statement = select(AlertRule).where(AlertRule.id.in_(request.rule_ids), AlertRule.user_id == user.id)
    rules = session.exec(statement).all()

    count = 0
    for rule in rules:
        session.delete(rule)
        count += 1

    session.commit()
    return {"message": f"Deleted {count} rules", "count": count}

@app.delete("/alerts/rule/{rule_id}")
def delete_alert_rule(rule_id: int, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    user = _verify_token(authorization, session)
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.user_id != user.id:
        raise HTTPException(status_code=403, detail="Rule does not belong to authenticated user")
    session.delete(rule)
    session.commit()
    return {"message": "Alert rule deleted"}

@app.get("/alerts/{user_id}/events", response_model=List[AlertEvent])
def get_alert_events(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except: return []
    _require_user_id(uid, authorization, session)
    service = PortableAlertsService(session)
    return service.get_recent_events(uid)

@app.post("/alerts/{user_id}/evaluate")
def evaluate_alerts(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except: return {"error": "Invalid user_id"}
    _require_user_id(uid, authorization, session)
    service = PortableAlertsService(session)
    events = service.evaluate_all_rules(uid)
    return {"message": f"Evaluated rules. {len(events)} new events triggered.", "events": events}

# --- NEWS ROUTES ---
# Best-effort per-process throttling for on-demand ingestion (no scheduler required).
_news_refresh_attempts = {}
NEWS_REFRESH_COOLDOWN_SECONDS = 60


async def _refresh_user_news(uid: int, session: Session, explicit: bool = False):
    now = time.monotonic()
    last = _news_refresh_attempts.get(uid)
    if last is not None and now - last < NEWS_REFRESH_COOLDOWN_SECONDS:
        if explicit:
            remaining = max(1, int(NEWS_REFRESH_COOLDOWN_SECONDS - (now - last)))
            raise HTTPException(status_code=429, detail=f"News refresh available in {remaining} seconds.")
        return 0
    # Set before awaiting network I/O to avoid concurrent requests starting the
    # same refresh. Remove expired entries so this map stays bounded over time.
    for key, attempted in list(_news_refresh_attempts.items()):
        if now - attempted >= NEWS_REFRESH_COOLDOWN_SECONDS:
            _news_refresh_attempts.pop(key, None)
    _news_refresh_attempts[uid] = now
    try:
        return await PortableNewsService(session).ingest_news_for_user(uid)
    except Exception as exc:
        session.rollback()
        logger.exception("News ingestion failed for user %s", uid)
        raise HTTPException(status_code=503, detail="Market news is temporarily unavailable. Please retry shortly.") from exc

@app.get("/news/{user_id}", response_model=List[NewsArticle])
async def get_news(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except:
        return []
    _require_user_id(uid, authorization, session)
    
    # 1. Get symbols from holdings
    holdings = session.exec(select(Holding).where(Holding.user_id == uid)).all()
    symbols = [h.symbol for h in holdings]
    
    # 2. Get symbols from alerts (to show news for watched stocks)
    rules = session.exec(select(AlertRule).where(AlertRule.user_id == uid)).all()
    for r in rules:
        if r.symbol and r.symbol not in symbols:
            symbols.append(r.symbol)

    if not symbols:
        return []
    
    statement = select(NewsArticle).where(NewsArticle.symbol.in_(symbols)).order_by(NewsArticle.published_at.desc()).limit(20)
    results = session.exec(statement).all()
    if not results:
        # The Community deployment does not start the standalone news scheduler.
        # Bootstrap an empty feed through the existing authenticated read route.
        await _refresh_user_news(uid, session)
        results = session.exec(statement).all()
    logger.debug(f"Returning {len(results)} news articles for user {uid} and symbols {symbols}")
    return results

@app.post("/news/{user_id}/ingest")
async def ingest_news(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except: return {"error": "Invalid user_id"}
    _require_user_id(uid, authorization, session)
    
    count = await _refresh_user_news(uid, session, explicit=True)
    return {"message": f"News ingestion complete. {count} new stories stored.",
            "last_refresh": datetime.now(timezone.utc).isoformat()}

# --- ANALYTICS ROUTES ---
@app.get("/analytics/{user_id}", response_model=Optional[AnalyticsSummary])
def get_analytics(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except: return None
    _require_user_id(uid, authorization, session)
    service = PortableAnalyticsService(session)
    return service.get_latest_summary(uid)

@app.post("/analytics/{user_id}/calculate")
async def calculate_analytics(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except: return {"error": "Invalid user_id"}
    _require_user_id(uid, authorization, session)
    
    portfolio_service = PortablePortfolioService(session)
    analytics_service = PortableAnalyticsService(session)
    price_service = PortablePriceService(session)
    
    # 1. Force Bulk Price Refresh for all holdings (this will also update missing names in cache)
    holdings = session.exec(select(Holding).where(Holding.user_id == uid)).all()
    symbols = [h.symbol for h in holdings]
    
    # Bulk fetch prices (Force refresh during manual calculation)
    # This also auto-resolves names and updates cache efficiently
    await price_service.get_prices_for_symbols(symbols, refresh_stale=True)
    
    # Commit all metadata updates once
    session.commit()

    # Await the async portable portfolio summary
    summary = await portfolio_service.get_portfolio_summary(uid)
    new_summary = analytics_service.calculate_and_save_summary(
        uid,
        summary.get('total_valuation', 0),
        summary.get('holdings', [])
    )
    
    # Trigger Alert Evaluation
    alerts_service = PortableAlertsService(session)
    alerts_service.evaluate_all_rules(uid)
    
    # Update user refresh timestamp in DB
    user = session.get(User, uid)
    if user:
        user.last_intelligence_refresh = datetime.now()
        session.add(user)
        session.commit()
        session.refresh(user)
    
    return new_summary

# --- AI INSIGHTS ---
@app.get("/insights/{user_id}")
def get_insights(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except: return []
    _require_user_id(uid, authorization, session)
    insights = session.exec(select(Insight).where(Insight.user_id == uid).order_by(Insight.timestamp.desc())).all()
    return insights

@app.post("/insights/{user_id}/generate")
async def generate_insights(user_id: str, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    try:
        uid = int(user_id)
    except: return {"error": "Invalid user_id"}
    _require_user_id(uid, authorization, session)
    
    # Check Cooldown
    user = session.get(User, uid)
    if user and user.last_intelligence_refresh:
        diff = datetime.now() - user.last_intelligence_refresh
        if timedelta(seconds=10) < diff < timedelta(minutes=1):
             raise HTTPException(status_code=429, detail="Refresh cooldown active (1 min).")

    portfolio_service = PortablePortfolioService(session)
    insight_service = PortableInsightService(session)
    # Await the portfolio summary (async)
    summary = await portfolio_service.get_portfolio_summary(uid)
    change_summary = {"events": [{"type": "VALUE", "metadata": {"change_pct": summary.get('total_gain_loss_pct', 0)}}]}
    # insights generation may involve async calls - await directly
    new_insights = await insight_service.generate_structured_insights(uid, change_summary, summary)

    # Update user's last refresh timestamp
    user = session.get(User, uid)
    if user:
        user.last_intelligence_refresh = datetime.now()
        session.add(user)
        session.commit()
        session.refresh(user)

    return new_insights

class TargetAllocationRequest(BaseModel):
    user_id: int
    allocation: dict # symbol: target_percentage
    drift_sensitivity: Optional[float] = 5.0

@app.post("/auth/target-allocation")
def set_target_allocation(request: TargetAllocationRequest, authorization: Optional[str] = Header(None), session: Session = Depends(get_session)):
    user = _require_user_id(request.user_id, authorization, session)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Validate allocation total (sum should be <= 100%)
    total = sum(request.allocation.values())
    if total > 100:
        raise HTTPException(status_code=400, detail="Total allocation cannot exceed 100%")
        
    user.target_allocation = json.dumps(request.allocation)
    if request.drift_sensitivity is not None:
        try:
            setattr(user, "drift_sensitivity", request.drift_sensitivity)
        except: pass
        
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"message": "Target allocation updated", "allocation": request.allocation, "drift_sensitivity": user.drift_sensitivity}

# --- MARKET CONTEXT ---
_market_context_cache = {"data": None, "timestamp": 0}
MARKET_CONTEXT_TTL = 15 * 60 # 15 minutes

@app.get("/market/context")
async def get_market_context(session: Session = Depends(get_session)):
    """
    Returns performance data for Nifty 50 and Nifty Next 50.
    Includes simple in-memory caching to avoid aggressive provider hits.
    """
    import time
    now = time.time()
    if _market_context_cache["data"] and (now - _market_context_cache["timestamp"] < MARKET_CONTEXT_TTL):
        return _market_context_cache["data"]

    price_service = PortablePriceService(session)
    nifty50_sym = getattr(settings, 'NIFTY50_SYMBOL', '^NSEI')
    nifty_next50_sym = getattr(settings, 'NIFTY_NEXT50_SYMBOL', '^NSMIDCP')

    # Fetch Nifty 50
    nifty50_data = await price_service.get_price_with_metadata(nifty50_sym)
    nifty50_price = nifty50_data.get('price')
    nifty50_prev = nifty50_data.get('prev_close')
    nifty50_change = ((nifty50_price - nifty50_prev) / nifty50_prev * 100) if nifty50_price and nifty50_prev else 0

    # Fetch Nifty Next 50 with faster fallback logic
    nifty_next50_data = await price_service.get_price_with_metadata(nifty_next50_sym)
    nifty_next50_price = nifty_next50_data.get('price')
    nifty_next50_prev = nifty_next50_data.get('prev_close')

    if nifty_next50_price is None:
        fallbacks = getattr(settings, 'NIFTY_NEXT50_FALLBACKS', [])
        if fallbacks:
            for alt in fallbacks:
                alt_data = await price_service.get_price_with_metadata(alt)
                if alt_data.get('price'):
                    nifty_next50_price = alt_data.get('price')
                    nifty_next50_prev = alt_data.get('prev_close')
                    nifty_next50_sym = alt
                    break

    nifty_next50_change = ((nifty_next50_price - nifty_next50_prev) / nifty_next50_prev * 100) if nifty_next50_price and nifty_next50_prev else 0

    response_data = {
        "indices": [
            {
                "name": "Nifty 50",
                "symbol": nifty50_sym,
                "current_price": nifty50_price,
                "change_pct": nifty50_change
            },
            {
                "name": "Nifty Next 50",
                "symbol": nifty_next50_sym,
                "current_price": nifty_next50_price,
                "change_pct": nifty_next50_change
            }
        ],
        "timestamp": datetime.now().isoformat()
    }

    _market_context_cache["data"] = response_data
    _market_context_cache["timestamp"] = now
    return response_data


# --- REFERENCE DATA ---
@app.get("/reference/stocks")
def get_stocks_reference():
    ref_service = ReferenceDataService()
    return ref_service.get_all_stocks()

@app.get("/reference/mutualfunds")
def get_mfs_reference():
    ref_service = ReferenceDataService()
    return ref_service.get_all_mfs()

# --- ADMIN / SYSTEM ---
@app.get("/admin/shutdown")
@app.post("/admin/shutdown")
async def shutdown_server(authorization: Optional[str] = Header(None)):
    """
    Programmatic shutdown for local development.
    Requires the same AUTH_SECRET_KEY bearer token used for API auth so this
    endpoint cannot be triggered anonymously in a hosted deployment.
    Works reliably on Windows.
    """
    import os
    import signal
    # Require a valid bearer token whose raw value matches AUTH_SECRET_KEY.
    # This is intentionally simpler than _verify_token — no user lookup needed,
    # just a shared-secret check so an anonymous internet request can't kill the process.
    expected_secret = os.getenv("AUTH_SECRET_KEY", os.getenv("SECRET_KEY", ""))
    provided = (authorization or "").removeprefix("Bearer ").strip()
    if not expected_secret or not provided or not hmac.compare_digest(provided, expected_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
    logger.info("Shutdown requested via API...")
    
    async def delayed_exit():
        await asyncio.sleep(0.5)
        # On Windows, signal.CTRL_C_EVENT is the most reliable for uvicorn
        sig = getattr(signal, "CTRL_C_EVENT", signal.SIGINT)
        os.kill(os.getpid(), sig)
    
    asyncio.create_task(delayed_exit())
    return {"message": "Server is shutting down... you can close this tab now."}

if __name__ == "__main__":
    # Enable graceful shutdown timeout (5 seconds)
    uvicorn.run(app, host="0.0.0.0", port=8000, timeout_graceful_shutdown=5)
