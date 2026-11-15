"""FinReview 2.0 protected research APIs (never trade real money)."""
from datetime import datetime, date
from typing import Optional, Literal
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


@router.post("/market/csv/research")
async def research_uploaded_csv(request: Request, user: User = Depends(current_user)):
    """Research user-supplied daily candles without any broker authorization.

    The submitted bytes are validation-only and never written to a database.
    No symbols, source names, tokens or broker credentials are accepted here.
    """
    from .market_data import MarketDataError
    from .research_csv import analyze_csv_bytes
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 3_000_000:
            raise HTTPException(413, "CSV exceeds 3 MB")
        chunks.append(chunk)
    try:
        return analyze_csv_bytes(b"".join(chunks))
    except MarketDataError as exc:
        raise HTTPException(422, str(exc))


@router.get("/market/analyze-cached")
def analyze_cached(provider: str, instrument: str, strategy: str = "breakout",
                   user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Read-only deterministic research on last *completed* cached daily candles.

    No new exchange data is downloaded, no signal is persisted, and no paper
    or live order is created. Lack of fresh enough bars is visible to callers.
    """
    from datetime import datetime, timezone, time, timedelta
    from zoneinfo import ZoneInfo
    from .candle_cache import read_candles
    from .strategies import breakout_observation, sma_trend_observation
    from .market_data import MarketDataError
    if provider not in ("upstox", "fyers", "zerodha", "csv") or not instrument or len(instrument) > 96:
        raise HTTPException(422, "Invalid provider or instrument")
    if strategy not in ("breakout", "sma_trend"):
        raise HTTPException(422, "Unsupported strategy")
    now = datetime.now(timezone.utc)
    cache = read_candles(session, user.id, provider, instrument,
                         now - timedelta(days=450), now)
    # Exclude the current Indian trading day until the next day. Incomplete
    # intraday bars must never be used as completed daily candles.
    today_ist = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
    completed = [c for c in cache["bars"]
                 if c.timestamp.astimezone(ZoneInfo("Asia/Kolkata")).date() < today_ist]
    if not completed:
        return {"status": "NO_DATA", "strategy": strategy,
                "instrument": instrument, "provider": provider,
                "analysis": None, "execution_mode": "RESEARCH_ONLY"}
    try:
        observation = (breakout_observation(completed) if strategy == "breakout"
                       else sma_trend_observation(completed))
    except MarketDataError:
        raise HTTPException(422, "Cached candles failed research validation")
    return {"status": "INSUFFICIENT_DATA" if observation is None else "READY",
            "provider": provider, "instrument": instrument, "strategy": strategy,
            "bars_available": len(completed), "adjustment": "provider_raw_unverified",
            "coverage": "unverified_partial",
            "analysis": None if observation is None else {
                "kind": observation.kind, "state": observation.status,
                "as_of": observation.as_of, "reference_price": observation.price,
                "trigger": observation.trigger, "invalidation": observation.invalidation,
                "atr": observation.atr, "rationale": observation.rationale},
            "execution_mode": "RESEARCH_ONLY"}


# Broker preference is non-secret. A selection does not grant API access and
# deliberately does not accept raw tokens from the browser.
class BrokerSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    historical_provider: Literal["fyers", "upstox", "zerodha"]
    quotes_provider: Literal["fyers", "upstox", "zerodha"]

@router.get("/market/preference")
def read_broker_preference(user: User = Depends(current_user),
                           session: Session = Depends(get_session)):
    from .provider_preferences import selected_brokers
    return selected_brokers(session, user.id)

@router.put("/market/preference")
def set_broker_preference(request: BrokerSelection, user: User = Depends(current_user),
                          session: Session = Depends(get_session)):
    from .provider_preferences import choose_brokers, selected_brokers
    choose_brokers(session, user.id, request.historical_provider, request.quotes_provider)
    return selected_brokers(session, user.id)


# Broker connections: metadata inspection and deletion only.
# OAuth handoff and all secret-accepting public routes remain disabled.
@router.get("/market/connections")
def broker_connection_status(user: User = Depends(current_user),
                             session: Session = Depends(get_session)):
    from .credential_vault import status
    return status(session, user.id)

@router.delete("/market/connections/{provider}")
def disconnect_broker(provider: Literal["fyers", "upstox", "zerodha"],
                      user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    from .credential_vault import disconnect
    return disconnect(session, user.id, provider)


# FYERS individual application is restricted to its creator. The one permitted
# staging user ID must be configured explicitly; no shared/public connection.
def _fyers_staging_owner(user_id=None):
    import os
    owner=os.getenv("FINREVIEW_FYERS_TEST_USER_ID","")
    return (os.getenv("FINREVIEW_ENV","").lower()=="staging" and
            os.getenv("FINREVIEW_V2_OAUTH_ENABLED","false").lower()=="true" and
            owner.isdecimal() and int(owner)>0 and
            (user_id is None or user_id==int(owner)))


@router.get("/market/fyers/test-availability")
def fyers_test_availability(user: User = Depends(current_user)):
    """Nonsecret user-scoped FYERS staging capability, not an entitlement claim."""
    return {"available":_fyers_staging_owner(user.id),
            "mode":"PERSONAL_STAGING_ONLY", "live_validated":False}


@router.post("/market/oauth/{provider}/start")
def start_broker_authorization(provider: Literal["fyers", "upstox", "zerodha"],
                               user: User = Depends(current_user),
                               session: Session = Depends(get_session)):
    from .broker_oauth import begin, BrokerAuthError
    if provider!="fyers" or not _fyers_staging_owner(user.id):
        raise HTTPException(403, "Broker login unavailable for this staging account")
    try:
        return begin(session, user.id, provider)
    except BrokerAuthError:
        raise HTTPException(503, "FYERS authorization unavailable or rate-limited")


@router.get("/market/oauth/{provider}/callback")
async def broker_authorization_callback(provider: Literal["fyers", "upstox", "zerodha"],
                                        request: Request,
                                        session: Session = Depends(get_session)):
    """FYERS GET callback; no tokens, codes, or state in response or redirect.

    Render edge/proxy logs are outside app control; production browser login
    remains forbidden until the platform's URL logging policy is verified.
    """
    from fastapi.responses import HTMLResponse
    from .broker_oauth import BrokerAuthError
    headers={"Cache-Control":"no-store","Pragma":"no-cache",
             "Referrer-Policy":"no-referrer",
             "X-Content-Type-Options":"nosniff",
             "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}
    def page(message):
        # No reflected request values: static server-owned content only.
        return HTMLResponse("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
              "<meta name='referrer' content='no-referrer'><title>FYERS connection</title></head>"
              "<body style='font:16px system-ui;max-width:600px;margin:5rem auto;padding:1rem'>"
              "<h1>FinReview FYERS test</h1><p>"+message+"</p>"
              "<p>Close this page and return to FinReview Research Lab to check status.</p>"
              "</body></html>",headers=headers)
    if provider!="fyers" or not _fyers_staging_owner():
        raise HTTPException(403, "Broker authentication disabled")
    params=request.query_params
    state=params.get("state")
    code=params.get("auth_code")
    if not state or not code:
        return page("Authorization was not completed. Please restart the connection.")
    # State is user-scoped, one-time and expires in 5 minutes.
    from .broker_oauth import claim_state, exchange_code, store_oauth_credentials
    from .broker_token_expiry import conservative_expiry
    from datetime import datetime, timezone
    try:
        user_id=claim_state(session,"fyers",state)
        if not _fyers_staging_owner(user_id):
            return page("This FYERS app is not enabled for this staging account.")
        credentials=await exchange_code("fyers",code)
        expiry=conservative_expiry("fyers",datetime.now(timezone.utc))
        store_oauth_credentials(session, user_id, "fyers", credentials, expires_at=expiry)
        # Redirect only to the fixed, operator-controlled staging UI.
        # Never forward request parameters, auth_code, state or broker tokens.
        from fastapi.responses import RedirectResponse
        return RedirectResponse(
            url="https://finreview-v201-staging-ui.onrender.com/intelligence.html#fyersConnectionPanel",
            status_code=303,
            headers=headers,
        )
    except (BrokerAuthError,RuntimeError,ValueError):
        return page("Authorization was unsuccessful or expired. Return to Research Lab and try again.")


@router.get("/market/fyers/symbols")
async def fyers_personal_symbols(
    q: str = "",
    user: User = Depends(current_user),
):
    """Owner-only symbol discovery from FYERS's public NSE cash-market master."""
    import os
    from fastapi.responses import JSONResponse
    from .fyers_symbols import search_symbols
    if (not _fyers_staging_owner(user.id) or
        os.getenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","false").lower()!="true"):
        raise HTTPException(403, "Personal symbol search unavailable")
    if len(q.strip()) < 2 or len(q) > 64:
        return JSONResponse({"results":[]},headers={"Cache-Control":"no-store"})
    try:
        results=await search_symbols(q)
    except Exception:
        raise HTTPException(503,"FYERS instrument directory temporarily unavailable")
    return JSONResponse({"results":results,"exchange":"NSE","segment":"EQUITY",
                         "source":"FYERS_SYMBOL_MASTER"},
                        headers={"Cache-Control":"no-store, private"})


@router.get("/market/fyers/research")
async def fyers_personal_research(
    instrument: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Owner-only personal broker research; no shared app or trading access."""
    import os
    import httpx
    from .fyers_data import FyersAuthenticationError
    from fastapi.responses import JSONResponse
    from .broker_research import research_from_fyers
    from .market_data import MarketDataError
    if (not _fyers_staging_owner(user.id) or
        os.getenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","false").lower()!="true"):
        raise HTTPException(403, "Personal broker research unavailable")
    try:
        data=await research_from_fyers(session,user.id,instrument)
        return JSONResponse(data,headers={"Cache-Control":"no-store, private",
                                           "Pragma":"no-cache",
                                           "Referrer-Policy":"no-referrer"})
    except MarketDataError:
        raise HTTPException(422,"FYERS returned invalid or unsupported historical data.")
    except (FyersAuthenticationError, ValueError):
        raise HTTPException(409,"Your FYERS session is unavailable or expired. Reconnect and retry.") from None
    except httpx.RequestError:
        raise HTTPException(502,"Unable to reach FYERS. Try again shortly.") from None
    except Exception:
        # Never reflect upstream URLs, account credentials, auth tokens or data.
        raise HTTPException(503,"FYERS research data temporarily unavailable")


@router.post("/market/fyers/test-data")
async def fyers_test_market_data(user: User = Depends(current_user),
                                 session: Session = Depends(get_session)):
    """Personal read-only staging smoke check, one fixed equity instrument.

    Never accepts symbols, dates or credentials from the browser. Does not
    store data, place orders, or expose prices, fingerprint, or broker identity.
    """
    import os
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from .market_smoke import check_stored_market_access
    if (not _fyers_staging_owner(user.id) or
        os.getenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","false").lower()!="true"):
        raise HTTPException(403, "Personal FYERS data test disabled")
    # Completed calendar days only; weekend gaps are legitimate, not filled.
    end_date=datetime.now(ZoneInfo("Asia/Kolkata")).date()-timedelta(days=1)
    start_date=end_date-timedelta(days=6)
    try:
        result=await check_stored_market_access(
            session, user.id, "fyers",
            history_instrument="NSE:INFY-EQ", quote_instrument="NSE:INFY-EQ",
            start=start_date, end=end_date)
        return {"provider":"fyers","instrument":"NSE:INFY-EQ",
                "profile":result.profile,"history":result.history,
                "quotes":result.quotes,"history_bars":result.history_bars,
                "history_start":start_date.isoformat(),
                "history_end":end_date.isoformat(),
                "data_rights":"NOT_ASSESSED",
                "quote_exchange_time":"NOT_VERIFIED",
                "trading_enabled":False,"stored":False}
    except Exception:
        # Never echo the underlying broker response, token or DB exception.
        raise HTTPException(503, "Read-only FYERS market test unavailable")


@router.post("/market/fyers/test-profile")
async def fyers_test_profile(user: User = Depends(current_user),
                             session: Session = Depends(get_session)):
    """Read-only owner-scoped profile check; no broker identity or token returned."""
    if not _fyers_staging_owner(user.id):
        raise HTTPException(403, "FYERS test disabled for this account")
    from .credential_vault import read_trusted
    from .session_probe import probe_credentials
    try:
        credentials=read_trusted(session,user.id,"fyers","quotes")
        data=await probe_credentials("fyers",credentials)
        return {"authenticated":data["profile_authenticated"],
                "historical_entitlement":"UNVERIFIED","quotes_entitlement":"UNVERIFIED",
                "no_real_orders":True}
    except Exception:
        raise HTTPException(503,"FYERS read-only profile verification unavailable")
