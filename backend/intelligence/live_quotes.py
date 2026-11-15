"""Provider-neutral read-only LTP snapshots. Never label receipt time as trade time.

For security, callers supply authorized provider clients. No token is exposed in
responses or stored; missing instruments and unsafe prices fail closed.
"""
from dataclasses import dataclass
import math
from .market_data import MarketDataError

@dataclass(frozen=True)
class Quote:
    provider: str
    instrument: str
    price: float
    source_timestamp: str | None = None

    def as_json(self):
        return {"provider": self.provider, "instrument": self.instrument,
                "last_price": self.price, "source_timestamp": self.source_timestamp,
                "timestamp_verified": self.source_timestamp is not None,
                "execution_mode": "RESEARCH_ONLY"}

def _price(value):
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketDataError("Malformed last price") from exc
    if not math.isfinite(n) or n <= 0:
        raise MarketDataError("Invalid last price")
    return n

def normalize_upstox(data, instruments):
    # API's map keys can use exchange:tradingsymbol rather than the input key;
    # match exact instrument_token to avoid assigning another instrument's LTP.
    if not isinstance(data, dict):
        raise MarketDataError("Malformed Upstox quote payload")
    lookup = {}
    for row in data.values():
        if isinstance(row, dict) and isinstance(row.get("instrument_token"), str):
            lookup[row["instrument_token"]] = row
    result = []
    for instrument in instruments:
        row = lookup.get(instrument)
        if row is None:
            raise MarketDataError("Upstox quote missing requested instrument")
        result.append(Quote("upstox", instrument, _price(row.get("last_price"))))
    return result

def normalize_fyers(payload, instruments):
    data = payload.get("d") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise MarketDataError("Malformed FYERS quote payload")
    lookup = {}
    for row in data:
        if isinstance(row, dict) and row.get("s") == "ok" and isinstance(row.get("n"), str):
            lookup[row["n"]] = row.get("v")
    result=[]
    for instrument in instruments:
        row = lookup.get(instrument)
        if not isinstance(row, dict):
            raise MarketDataError("FYERS quote missing requested instrument")
        result.append(Quote("fyers", instrument, _price(row.get("lp"))))
    return result

def normalize_zerodha(data, instruments):
    if not isinstance(data, dict):
        raise MarketDataError("Malformed Zerodha quote payload")
    result=[]
    for instrument in instruments:
        row=data.get(instrument)
        if not isinstance(row, dict):
            raise MarketDataError("Zerodha quote missing requested instrument")
        result.append(Quote("zerodha", instrument, _price(row.get("last_price"))))
    return result

async def load_quotes(provider: str, instruments: list[str], *, upstox=None, fyers=None, zerodha=None):
    if not isinstance(instruments,list) or not instruments or len(instruments) > 50:
        raise MarketDataError("Choose 1 to 50 instruments")
    if len(set(instruments)) != len(instruments):
        raise MarketDataError("Duplicate instruments")
    if provider == "upstox":
        from .upstox_data import UpstoxReadOnlyData
        client=upstox or UpstoxReadOnlyData(purpose="quotes")
        return normalize_upstox(await client.quotes(instruments), instruments)
    if provider == "fyers":
        from .fyers_data import FyersReadOnlyData
        client=fyers or FyersReadOnlyData()
        return normalize_fyers(await client.quotes(instruments), instruments)
    if provider == "zerodha":
        from .zerodha_data import ZerodhaReadOnlyData
        client=zerodha or ZerodhaReadOnlyData()
        return normalize_zerodha(await client.quotes(instruments), instruments)
    raise MarketDataError("Choose FYERS, Upstox or Zerodha")
