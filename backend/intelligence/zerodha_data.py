"""Read-only Kite Connect V3 data adapter; no orders, positions or trading methods.

Zerodha's Personal (free) API does not include market-data access.
Historical candles / quotes require a data-enabled Kite Connect subscription.
"""
import os
import re
from datetime import date
from zoneinfo import ZoneInfo
import httpx
from .market_data import MarketDataError, validate_candles, validate_range

API_ROOT = "https://api.kite.trade"
IST = ZoneInfo("Asia/Kolkata")


class ZerodhaReadOnlyData:
    def __init__(self, api_key=None, access_token=None, http_client=None):
        self.api_key = api_key if api_key is not None else os.getenv("ZERODHA_API_KEY", "")
        self.token = access_token if access_token is not None else os.getenv("ZERODHA_ACCESS_TOKEN", "")
        self.client = http_client
        if not self.api_key or not self.token:
            raise MarketDataError("Zerodha data-enabled Kite Connect credentials not configured")

    async def _get(self, path, params):
        headers = {"Authorization": f"token {self.api_key}:{self.token}",
                   "X-Kite-Version": "3"}
        if self.client is None:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.get(API_ROOT + path, params=params, headers=headers)
        else:
            response = await self.client.get(API_ROOT + path, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise MarketDataError("Zerodha market data unavailable or session expired")
        return payload.get("data")

    async def history(self, instrument_token: str, start: date, end: date):
        if not isinstance(instrument_token, str) or not re.fullmatch(r"[0-9]{1,18}", instrument_token):
            raise MarketDataError("Use the numeric Kite instrument_token from the instrument master")
        validate_range(start, end, max_days=365)
        params = {"from": f"{start.isoformat()} 00:00:00",
                  "to": f"{end.isoformat()} 23:59:59",
                  "continuous": 0, "oi": 0}
        data = await self._get(f"/instruments/historical/{instrument_token}/day", params)
        if not isinstance(data, dict) or not isinstance(data.get("candles"), list):
            raise MarketDataError("Malformed Zerodha historical response")
        candles = validate_candles(data["candles"])
        return [bar for bar in candles if start <= bar.timestamp.astimezone(IST).date() <= end]

    async def quotes(self, instruments: list[str]):
        if not instruments or len(instruments) > 50:
            raise MarketDataError("1 to 50 exchange:tradingsymbol symbols required")
        if any(not isinstance(x, str) or not re.fullmatch(r"[A-Z0-9_]+:[A-Za-z0-9_+&.-]{1,60}", x)
               for x in instruments):
            raise MarketDataError("Invalid Kite exchange:tradingsymbol")
        data = await self._get("/quote/ltp", [("i", value) for value in instruments])
        if not isinstance(data, dict):
            raise MarketDataError("Malformed Zerodha quote response")
        # Missing instruments are omitted by Kite: retain that information.
        return data
