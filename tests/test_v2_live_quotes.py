import sys
from pathlib import Path
import httpx
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from intelligence.market_data import MarketDataError
from intelligence.live_quotes import load_quotes, normalize_upstox, normalize_fyers, normalize_zerodha
from intelligence.upstox_data import UpstoxReadOnlyData

@pytest.mark.asyncio
async def test_upstox_v3_ltp_uses_separate_market_access_token():
    seen=[]
    def serve(request):
        seen.append(request)
        return httpx.Response(200,json={"status":"success","data":{
            "NSE_EQ:INFY":{"instrument_token":"NSE_EQ|INE009A01021","last_price":1520.5,"ltq":2}
        }})
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        up=UpstoxReadOnlyData(token="test-access-token",http_client=client,purpose="quotes")
        q=await load_quotes("upstox",["NSE_EQ|INE009A01021"],upstox=up)
    assert q[0].price==1520.5 and not q[0].as_json()["timestamp_verified"]
    assert str(seen[0].url).endswith("instrument_key=NSE_EQ%7CINE009A01021")
    assert seen[0].headers["Authorization"]=="Bearer test-access-token"

@pytest.mark.asyncio
async def test_fyers_and_zerodha_normalization():
    class Fyers:
        async def quotes(self, instruments):
            return {"s":"ok","d":[{"n":"NSE:INFY-EQ","s":"ok","v":{"lp":1500.25}}]}
    class Zerodha:
        async def quotes(self, instruments):
            return {"NSE:INFY":{"last_price":1501.25}}
    assert (await load_quotes("fyers",["NSE:INFY-EQ"],fyers=Fyers()))[0].price==1500.25
    assert (await load_quotes("zerodha",["NSE:INFY"],zerodha=Zerodha()))[0].price==1501.25

@pytest.mark.parametrize("provider,data,symbols",[
    ("upstox",{"OTHER":{"instrument_token":"NSE_EQ|OTHER","last_price":42}},["NSE_EQ|MISSING"]),
    ("fyers",{"d":[{"n":"NSE:A-EQ","s":"error","v":{"lp":100}}]},["NSE:A-EQ"]),
    ("zerodha",{"NSE:INFY":{"last_price":float("nan")}},["NSE:INFY"]),
])
def test_missing_or_invalid_quotes_fail_closed(provider,data,symbols):
    normalize={"upstox":normalize_upstox,"fyers":normalize_fyers,"zerodha":normalize_zerodha}
    with pytest.raises(MarketDataError):
        normalize[provider](data,symbols)

@pytest.mark.asyncio
async def test_provider_batch_rejects_duplicates():
    with pytest.raises(MarketDataError):
        await load_quotes("zerodha",["NSE:INFY","NSE:INFY"])
