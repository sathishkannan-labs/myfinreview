from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from intelligence.broker_token_expiry import conservative_expiry
IST=ZoneInfo("Asia/Kolkata")

@pytest.mark.parametrize("provider,hour,minute",[
    ("fyers",6,25),("zerodha",5,55),("upstox",3,25)
])
def test_conservative_next_daily_cutoff(provider,hour,minute):
    issued=datetime(2026,11,14,12,0,tzinfo=IST)
    result=conservative_expiry(provider,issued).astimezone(IST)
    assert result.day==15 and (result.hour,result.minute)==(hour,minute)

def test_pre_cutoff_expires_same_calendar_day():
    issued=datetime(2026,11,14,1,0,tzinfo=IST)
    result=conservative_expiry("upstox",issued).astimezone(IST)
    assert (result.day,result.hour,result.minute)==(14,3,25)

def test_naive_time_rejected():
    with pytest.raises(ValueError):
        conservative_expiry("fyers",datetime(2026,11,14,10,0))
