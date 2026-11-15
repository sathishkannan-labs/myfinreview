"""Private, read-only broker market-data verification. No orders or DB writes.

The caller supplies account-scoped credentials, a valid historical instrument
and a separate quote instrument; no symbols, prices, tokens, names or account IDs
are echoed in the result. A profile success alone is NOT a data entitlement.
"""
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from .market_data import MarketDataError, validate_range
from .session_probe import probe_credentials
from .providers import load_history
from .live_quotes import load_quotes
from .fyers_data import FyersReadOnlyData
from .upstox_data import UpstoxReadOnlyData
from .zerodha_data import ZerodhaReadOnlyData

@dataclass(frozen=True)
class MarketSmokeResult:
    broker: str
    profile: str
    history: str
    quotes: str
    history_bars: int
    identity_fingerprint: str | None
    trading_enabled: bool = False

    def as_json(self):
        return asdict(self)

def make_client(broker, credentials, *, http_client=None, purpose="quotes"):
    """Do not fall back to process-wide broker tokens in a multi-user test."""
    if not isinstance(credentials, dict) or not credentials.get("access_token"):
        raise MarketDataError("Account-scoped credentials required")
    token=credentials["access_token"]
    if broker=="fyers":
        app_id=credentials.get("app_id")
        if not app_id:
            raise MarketDataError("FYERS app ID missing")
        return FyersReadOnlyData(client_id=app_id,access_token=token,http_client=http_client)
    if broker=="zerodha":
        app_id=credentials.get("app_id")
        if not app_id:
            raise MarketDataError("Kite API key missing")
        return ZerodhaReadOnlyData(api_key=app_id,access_token=token,http_client=http_client)
    if broker=="upstox":
        return UpstoxReadOnlyData(token=token,http_client=http_client,
                                  purpose="history" if purpose=="historical" else "quotes")
    raise MarketDataError("Unsupported broker")

async def check_market_access(broker, *, profile_credentials, historical_credentials,
                              quote_credentials, history_instrument, quote_instrument,
                              start: date, end: date, profile_http=None,
                              history_client=None, quote_client=None) -> MarketSmokeResult:
    """Independent per-purpose checks; error details deliberately not returned.

    Does not promote a quote to exchange-time verified, and cannot validate
    storage/redistribution rights or true historical completeness.
    """
    validate_range(start,end,max_days=7)
    if end > date.today():
        raise MarketDataError("Market smoke checks cannot request future candles")
    profile, history, quotes="FAILED","NOT_RUN","NOT_RUN"
    fingerprint=None
    try:
        result=await probe_credentials(broker,profile_credentials,client=profile_http)
        profile="AUTHENTICATED"
        fingerprint=result["identity_fingerprint"]
    except Exception:
        return MarketSmokeResult(broker,profile,history,quotes,0,None)
    count=0
    try:
        data=await load_history(
            broker,history_instrument,start,end,
            **{broker:history_client or make_client(
                broker,historical_credentials,purpose="historical")})
        count=len(data.candles)
        history="VERIFIED_SAMPLE" if count else "EMPTY"
    except Exception:
        history="UNAVAILABLE"
    try:
        result=await load_quotes(
            broker,[quote_instrument],
            **{broker:quote_client or make_client(
                broker,quote_credentials,purpose="quotes")})
        quotes="VERIFIED_SAMPLE" if len(result)==1 and result[0].price>0 else "UNAVAILABLE"
    except Exception:
        quotes="UNAVAILABLE"
    return MarketSmokeResult(broker,profile,history,quotes,count,fingerprint)


async def check_stored_market_access(session, user_id, broker, *, history_instrument,
                                     quote_instrument, start, end,
                                     profile_http=None, history_client=None,
                                     quote_client=None):
    """Trusted private worker only; refuses live HTTP unless explicitly opted in."""
    import os
    from .credential_vault import read_trusted
    if os.getenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","false").lower()!="true":
        raise MarketDataError("Read-only broker diagnostics disabled")
    if not isinstance(user_id,int) or user_id <= 0:
        raise MarketDataError("Invalid account")
    historical=read_trusted(session,user_id,broker,"historical")
    quotes=read_trusted(session,user_id,broker,"quotes")
    return await check_market_access(
        broker,profile_credentials=quotes,historical_credentials=historical,
        quote_credentials=quotes,history_instrument=history_instrument,
        quote_instrument=quote_instrument,start=start,end=end,
        profile_http=profile_http,history_client=history_client,
        quote_client=quote_client)
