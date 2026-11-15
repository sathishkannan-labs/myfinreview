"""FYERS market-data-only adapter. Never handles trading or account orders.

Access is opt-in. Your FYERS data licence, session and non-trading app permissions
must be confirmed before enabling this provider.
"""
import os
import httpx

FYERS_BASE = "https://api-t1.fyers.in"
class FyersDataError(RuntimeError):
    pass

class FyersAuthenticationError(FyersDataError):
    pass


class FyersReadOnlyData:
    def __init__(self, client_id=None, access_token=None, http_client=None):
        self.client_id = client_id or os.getenv("FYERS_CLIENT_ID", "")
        self.token = access_token or os.getenv("FYERS_ACCESS_TOKEN", "")
        self.client = http_client
        if not self.client_id or not self.token:
            raise FyersDataError("FYERS read-only session not configured")
    async def _request(self, path, params):
        headers = {"Authorization": f"{self.client_id}:{self.token}"}
        if self.client is not None:
            response = await self.client.get(FYERS_BASE+path, params=params, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.get(FYERS_BASE+path, params=params, headers=headers)
        if response.status_code == 401:
            raise FyersAuthenticationError("FYERS authentication rejected")
        try:
            data = response.json()
        except ValueError:
            data = None
        # FYERS can return a token error in an otherwise successful HTTP response.
        if isinstance(data, dict) and data.get("s") != "ok" and data.get("code") in (-8, -15, -16, -17):
            raise FyersAuthenticationError("FYERS authentication rejected")
        response.raise_for_status()
        if not isinstance(data, dict) or data.get("s") != "ok":
            raise FyersDataError("FYERS market data unavailable")
        return data
    async def quotes(self, symbols: list[str]) -> dict:
        if not symbols or len(symbols) > 50:
            raise ValueError("Pass 1 to 50 symbols per request")
        if any(not isinstance(s, str) or len(s)>80 or not s for s in symbols):
            raise ValueError("Invalid symbol")
        return await self._request("/data/quotes", {"symbols": ",".join(symbols)})
    async def history(self, symbol: str, resolution="D", from_epoch=None, to_epoch=None):
        if not symbol or resolution not in ("D", "5", "15", "60"):
            raise ValueError("Invalid history request")
        if from_epoch is None or to_epoch is None or from_epoch >= to_epoch:
            raise ValueError("Valid epoch range required")
        return await self._request("/data/history", {
            "symbol": symbol, "resolution": resolution, "date_format": "0",
            "range_from": str(from_epoch), "range_to": str(to_epoch), "cont_flag": "1"
        })
