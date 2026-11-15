import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from intelligence.staging_smoke import permit_live, configure_parser

@pytest.mark.parametrize("live,environment,enabled",[
    (False,"staging","true"),(True,"production","true"),
    (True,"staging","false"),(True,"","true"),
])
def test_diagnostics_refuse_unsafe_activation(live,environment,enabled):
    with pytest.raises(ValueError):
        permit_live(live=live,environment=environment,enabled=enabled)

def test_private_staging_live_is_explicit():
    permit_live(live=True,environment="staging",enabled="true")

def test_cli_never_requires_credentials_in_args():
    args=configure_parser().parse_args([
        "--user-id","1","--broker","fyers",
        "--historical-instrument","NSE:INFY-EQ","--quote-instrument","NSE:INFY-EQ",
        "--start","2026-11-10","--end","2026-11-11"])
    assert not args.live
    assert "token" not in str(vars(args)).lower()
