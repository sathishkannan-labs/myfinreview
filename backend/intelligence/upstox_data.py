"""Upstox V3 historical candles using a read-only Analytics Token.

Documentation: https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
Live credentials are never logged or returned through API responses.
"""
import os
from datetime import date
from urllib.parse import quote
import httpx
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
from .market_data import validate_candles, validate_range, instrument_key, MarketDataError

UPSTOX_API = "https://api.upstox.com"

class UpstoxReadOnlyData:
    def __init__(self, token=None, http_client=None, purpose="history"):
        if purpose not in ("history", "quotes"):
            raise MarketDataError("Invalid market-data purpose")
        self.purpose = purpose
        token_env = "UPSTOX_ANALYTICS_TOKEN" if purpose == "history" else "UPSTOX_ACCESS_TOKEN"
        self.token = token if token is not None else os.getenv(token_env, "")
        self.client = http_client
        if not self.token:
            raise MarketDataError(f"{token_env} not configured")

    async def history(self, key: str, start: date, end: date, unit="days", interval=1):
        instrument_key(key)
        if not isinstance(start, date) or not isinstance(end, date):
            raise MarketDataError("Dates are required")
        # V3 max lookback per call: 1 decade for daily; one month
        # for <=15-minute intervals. We start with daily only.
        if unit != "days" or interval != 1:
            raise MarketDataError("Initial Upstox integration supports daily candles only")
        validate_range(start, end, max_days=3650)
        path = ("/v3/historical-candle/" + quote(key, safe="") +
                f"/days/1/{end.isoformat()}/{start.isoformat()}")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if self.client is None:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(UPSTOX_API+path, headers=headers)
        else:
            response = await self.client.get(UPSTOX_API+path, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise MarketDataError("Upstox historical data unavailable")
        rows = payload.get("data", {}).get("candles")
        if not isinstance(rows, list):
            raise MarketDataError("Upstox returned malformed candles")
        candles = validate_candles(rows)
        # Provider may include candles outside requested dates; drop them.
        return [c for c in candles if start <= c.timestamp.astimezone(IST).date() <= end]

    async def quotes(self, instruments: list[str]):
        """Upstox V3 LTP; a market-data-authorized access token is required.

        Analytics Tokens may be historical-data-only; they are deliberately
        not used to authenticate this endpoint.
        """
        if self.purpose != "quotes":
            raise MarketDataError("Instantiate UpstoxReadOnlyData(purpose='quotes')")
        if not isinstance(instruments, list) or not (1 <= len(instruments) <= 50):
            raise MarketDataError("Choose 1 to 50 instrument keys")
        for key in instruments:
            instrument_key(key)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        url = UPSTOX_API + "/v3/market-quote/ltp"
        params = {"instrument_key": ",".join(instruments)}
        if self.client is None:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.get(url, headers=headers, params=params)
        else:
            response = await self.client.get(url, headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise MarketDataError("Upstox quote session or market-data entitlement unavailable")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MarketDataError("Malformed Upstox LTP payload")
        return data
