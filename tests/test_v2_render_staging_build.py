import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from build_render_staging_frontend import validated_api_url, build, TARGET

@pytest.mark.parametrize("url",[
    "", "http://api.onrender.com", "https://example.com",
    "https://evil.onrender.com.attacker.com", "https://u:p@x.onrender.com",
    "https://x.onrender.com?access_token=abc", "https://x.onrender.com/foo",
])
def test_staging_build_rejects_unsafe_api_urls(url):
    with pytest.raises(ValueError):
        validated_api_url(url)

def test_build_injects_runtime_before_config():
    build("https://finreview-v201-staging-api.onrender.com")
    index=(TARGET/"index.html").read_text()
    research=(TARGET/"intelligence.html").read_text()
    runtime=(TARGET/"config.runtime.js").read_text()
    assert 'src="config.runtime.php"' not in index
    assert index.index('src="config.runtime.js"') < index.index('src="config.js"')
    assert research.index('src="./config.runtime.js"') < research.index('src="./config.js"')
    assert "https://finreview-v201-staging-api.onrender.com" in runtime
    assert "FINREVIEW_BROKER_VAULT_KEY" not in runtime
