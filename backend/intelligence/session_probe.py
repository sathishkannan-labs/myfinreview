"""Read-only broker session checks. No credential strings or PII leave this module.

A valid profile verifies authentication and broker identity, NOT market-data
entitlement. Probe results must never be treated as permission to trade.
"""
import os
import httpx
from .market_data import MarketDataError
from .credential_vault import read_trusted

PROFILE_URLS = {
    "fyers": "https://api-t1.fyers.in/api/v3/profile",
    "upstox": "https://api.upstox.com/v2/user/profile",
    "zerodha": "https://api.kite.trade/user/profile",
}


def _headers(provider: str, credentials: dict) -> dict:
    token = credentials.get("access_token")
    if not token:
        raise MarketDataError("Broker access token unavailable")
    if provider == "zerodha":
        app_id = credentials.get("app_id")
        if not app_id:
            raise MarketDataError("Kite API key unavailable")
        return {"Authorization": f"token {app_id}:{token}", "X-Kite-Version": "3"}
    if provider == "fyers":
        app_id = credentials.get("app_id")
        if not app_id:
            raise MarketDataError("FYERS client ID unavailable")
        return {"Authorization": f"{app_id}:{token}"}
    if provider == "upstox":
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    raise MarketDataError("Unsupported broker")


def _identity(provider: str, payload: dict) -> str:
    if not isinstance(payload, dict):
        raise MarketDataError("Malformed broker profile")
    if provider == "fyers":
        if payload.get("s") != "ok" or not isinstance(payload.get("data"), dict):
            raise MarketDataError("Broker profile request failed")
        data = payload["data"]
        ident = data.get("fy_id")
    else:
        if payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
            raise MarketDataError("Broker profile request failed")
        data = payload["data"]
        ident = data.get("user_id")
    if not isinstance(ident, str) or not ident.strip() or len(ident)>128:
        raise MarketDataError("Broker profile identity missing")
    if provider == "upstox" and data.get("broker", "UPSTOX") != "UPSTOX":
        raise MarketDataError("Broker identity mismatch")
    if provider == "zerodha" and data.get("broker", "ZERODHA") != "ZERODHA":
        raise MarketDataError("Broker identity mismatch")
    return ident


async def probe_credentials(provider: str, credentials: dict, *, client=None) -> dict:
    """Verify profile via official read-only endpoint. Return only a keyed hash.

    Does not include raw name, broker user ID, mobile, email or token. Profile
    access alone is NOT proof of quote/historical API subscription entitlement.
    """
    if provider not in PROFILE_URLS:
        raise MarketDataError("Unsupported broker")
    headers=_headers(provider, credentials)
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=12, follow_redirects=False) as http:
                response=await http.get(PROFILE_URLS[provider], headers=headers)
        else:
            response=await client.get(PROFILE_URLS[provider], headers=headers)
        response.raise_for_status()
        profile=_identity(provider, response.json())
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise MarketDataError("Broker profile validation failed") from None
    # HMAC requires a secret independent of broker and encryption keys;
    # fingerprint is stable within deployment but cannot be reversed to UCC.
    import hashlib, hmac
    secret=os.getenv("FINREVIEW_BROKER_IDENTITY_HMAC_KEY","")
    if len(secret)<32:
        raise MarketDataError("Broker identity verification key not configured")
    fingerprint=hmac.new(secret.encode(), f"{provider}:{profile}".encode(),
                         hashlib.sha256).hexdigest()
    return {"provider":provider, "profile_authenticated":True,
            "identity_fingerprint":fingerprint,
            "historical_entitlement":"UNVERIFIED",
            "quotes_entitlement":"UNVERIFIED",
            "trading_enabled":False}


async def probe_stored(session, user_id: int, provider: str, purpose: str, *, client=None):
    """Trusted worker only: fetch account-scoped credentials, never expose tokens."""
    if os.getenv("FINREVIEW_V2_BROKER_PROBES_ENABLED","false").lower()!="true":
        raise MarketDataError("Broker profile probes disabled")
    credentials=read_trusted(session,user_id,provider,purpose)
    return await probe_credentials(provider,credentials,client=client)
