"""Owner-only FYERS historical research: bridge existing adapters to existing strategies.

Not a public broker-data API, streaming feed, data redistribution, or trading.
Runs on-demand with the per-user encrypted OAuth token. Never persists prices.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .credential_vault import read_trusted
from .market_data import MarketDataError
from .market_smoke import make_client
from .providers import load_history
from .live_quotes import load_quotes
from .strategies import breakout_observation, sma_trend_observation
from .fyers_symbols import is_current_equity

IST=ZoneInfo("Asia/Kolkata")
# Backwards compatibility for existing test samples; live validation uses the
# current FYERS NSE Capital Market Symbol Master instead of hard-coded tickers.
INSTRUMENTS=("NSE:INFY-EQ","NSE:RELIANCE-EQ","NSE:HDFCBANK-EQ")


def _result(obs):
    if obs is None:
        return {"status":"INSUFFICIENT_DATA","observation":None}
    return {"status":"READY","observation":{
        "kind":obs.kind,"state":obs.status,"as_of":obs.as_of.isoformat(),
        "reference_price":obs.price,"trigger":obs.trigger,
        "invalidation":obs.invalidation,"atr":obs.atr,
        "rationale":obs.rationale}}


async def research_from_fyers(session, user_id:int, instrument:str, *, now=None,
                              history_client=None, quote_client=None):
    """User-scoped, read-only, small response; provider timestamps unverified."""
    try:
        valid_instrument = await is_current_equity(instrument)
    except ValueError:
        raise RuntimeError("FYERS instrument directory unavailable") from None
    if not valid_instrument:
        raise MarketDataError("Choose a current NSE equity from the FYERS symbol list")
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise MarketDataError("Timezone-aware reference required")
    today=now.astimezone(IST).date()
    end=today-timedelta(days=1)
    start=end-timedelta(days=160)
    historical=read_trusted(session,user_id,"fyers","historical")
    quotes=read_trusted(session,user_id,"fyers","quotes")
    dataset=await load_history("fyers",instrument,start,end,
        fyers=history_client or make_client("fyers",historical,purpose="historical"))
    # A daily candle timestamp is not evidence that today's session has closed.
    bars=[b for b in dataset.candles if b.timestamp.astimezone(IST).date()<today]
    if len({b.timestamp.astimezone(IST).date() for b in bars})!=len(bars):
        raise MarketDataError("FYERS returned duplicate daily sessions")
    if not bars:
        return {"status":"NO_DATA","provider":"fyers","instrument":instrument,
                "execution_mode":"RESEARCH_ONLY","stored":False,
                "historical_bars":0,"analysis":None,"quote":None}
    if len(bars)>150:
        bars=bars[-150:]
    last=bars[-1]
    if last.timestamp>now:
        raise MarketDataError("Provider historical time is in the future")
    # Quote is an optional independent snapshot, never used as a completed bar.
    quote=None
    quote_status="UNAVAILABLE"
    try:
        one=await load_quotes("fyers",[instrument],
            fyers=quote_client or make_client("fyers",quotes,purpose="quotes"))
        quote={"last_price":one[0].price,"timestamp_verified":False,
               "received_at":now.isoformat()}
        quote_status="SNAPSHOT_RECEIVED"
    except Exception:
        # Fail isolated: a quote outage must not suppress valid historical research.
        pass
    return {
        "status":"READY" if len(bars)>=50 else "INSUFFICIENT_DATA",
        "source":"FYERS_PERSONAL_ACCOUNT","provider":"fyers","instrument":instrument,
        "execution_mode":"RESEARCH_ONLY","stored":False,"trading_enabled":False,
        "rows":len(bars),"first_timestamp":bars[0].timestamp.isoformat(),
        "last_timestamp":last.timestamp.isoformat(),"last_close":last.close,
        "adjustment":"PROVIDER_RAW_UNVERIFIED",
        "exchange_calendar":"NOT_VERIFIED","exchange_timestamp_verified":False,
        "quote_status":quote_status,"quote":quote,
        "data_rights":"PERSONAL_ONLY_NOT_FOR_REDISTRIBUTION",
        "strategies":{"breakout":_result(breakout_observation(bars)),
                      "sma_trend":_result(sma_trend_observation(bars))}
    }
