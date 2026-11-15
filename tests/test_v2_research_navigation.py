"""Release stabilization: the v2 workspace must be discoverable and truthful."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HOME=(ROOT/"frontend/index.html").read_text(encoding="utf-8")
RESEARCH=(ROOT/"frontend/intelligence.html").read_text(encoding="utf-8")


def test_research_link_visible_in_main_navigation_and_dashboard():
    assert 'id="nav-research"' in HOME
    assert 'id="open-research-lab"' in HOME
    assert HOME.count('href="./intelligence.html"') >= 2


def test_v2_status_is_visible_and_does_not_claim_live_trading():
    assert "v2.0.1 Preview" in HOME
    assert "Broker login and live quotes are disabled" in HOME
    assert "No real orders" in HOME
    assert "FinReview v2.0.1 — Research Lab" in RESEARCH
    assert "FYERS individual-app login can be enabled only" in RESEARCH


def test_research_workspace_uses_existing_auth_and_apis():
    assert "finreview_token" in RESEARCH
    assert "/v2/signals" in RESEARCH
    assert "/v2/paper-account" in RESEARCH
    assert "/v2/market/fyers/research" in RESEARCH


def test_research_workflow_is_primary_and_experimental_controls_collapsed():
    assert 'id="labReport"' in RESEARCH
    assert 'id="labMetrics"' in RESEARCH
    assert 'id="downloadResearchReport"' in RESEARCH
    assert 'id="labOptional"' in RESEARCH
    assert RESEARCH.index('id="researchCsvForm"') < RESEARCH.index('id="labOptional"')
    assert "historical research report" in RESEARCH.lower()
    assert "not buy/sell signals" in RESEARCH
    assert "Date unavailable" in RESEARCH
    assert "Invalid Date" not in RESEARCH


def test_inline_research_javascript_syntax(tmp_path):
    import re
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    scripts = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", RESEARCH)
    assert scripts
    js = tmp_path / "research-lab.js"
    js.write_text(chr(10).join(scripts), encoding="utf-8")
    result = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_private_fyers_connect_is_not_open_to_other_brokers():
    assert 'id="connectFyers"' in RESEARCH
    assert 'id="refreshFyersStatus"' in RESEARCH
    assert 'id="probeFyersProfile"' in RESEARCH
    assert 'id="disconnectFyers"' in RESEARCH
    assert '/v2/market/fyers/test-availability' in RESEARCH
    assert '/v2/market/oauth/fyers/start' in RESEARCH
    assert 'u.hostname!==\'api-t1.fyers.in\'' in RESEARCH


def test_community_research_has_complete_no_broker_path():
    assert 'id="runDemoResearch"' in RESEARCH
    assert 'id="researchCsvInput"' in RESEARCH
    assert "async function analyzeHistoricalFile(file)" in RESEARCH
    assert "analyzeHistoricalFile(file);" in RESEARCH
    assert "data.strategies?.breakout?.observation" in RESEARCH
    assert "data.strategies?.sma_trend?.observation" in RESEARCH
    assert "id=\"labOverview\"" in RESEARCH
    assert "Historical data is over 7 days old" in RESEARCH
    assert 'id="downloadResearchReport"' in RESEARCH
    assert "lastReport=data" in RESEARCH


def test_developer_diagnostics_not_in_community_view_by_default():
    from html.parser import HTMLParser

    class DetailsParser(HTMLParser):
        found=False
        hidden=False
        def handle_starttag(self,tag,attrs):
            if tag=="details" and dict(attrs).get("id")=="labOptional":
                self.found=True
                self.hidden="hidden" in dict(attrs)
    parser=DetailsParser()
    parser.feed(RESEARCH)
    assert parser.found and parser.hidden
    assert "[hidden]{display:none!important}" in RESEARCH
    assert "diagnostics.hidden=!fyersTestAvailable" in RESEARCH


def test_nonfunctional_broker_preferences_removed_from_research_ui():
    assert 'id="providerForm"' not in RESEARCH
    assert 'id="historyProvider"' not in RESEARCH
    assert 'id="quoteProvider"' not in RESEARCH
    assert "loadProviderPreference()" not in RESEARCH
    assert 'id="liveBrokerResearch"' in RESEARCH
    assert 'id="runFyersResearch"' in RESEARCH
    assert "renderResearchReport(data,notice" in RESEARCH
    assert 'id="fyersResearchSymbol"' in RESEARCH
    assert "FYERS quote API snapshot" in RESEARCH
    assert "diagnostics.hidden=!fyersTestAvailable" in RESEARCH


def test_shared_report_is_outside_data_source_panels():
    from html.parser import HTMLParser
    class Layout(HTMLParser):
        stack=[]
        parent=None
        def handle_starttag(self,tag,attrs):
            d=dict(attrs)
            if tag in ("section","details"):
                if d.get("id")=="labReport":
                    self.parent=self.stack[-1] if self.stack else None
                self.stack.append(d.get("id") or tag)
        def handle_endtag(self,tag):
            if tag in ("section","details") and self.stack:
                self.stack.pop()
    # Ensure the CSV section is closed before the report begins.
    assert RESEARCH.index('</section>\\n<section id="labReport"') < RESEARCH.index('id="labOptional"')
    assert 'id="strategyResearchResults"' in RESEARCH
    assert 'id="csvResearchResults"' not in RESEARCH
    assert 'id="printResearchReport"' in RESEARCH
    assert "window.print()" in RESEARCH


def test_diagnostics_not_misleading_after_authenticated_fyers_research():
    assert "Historical data access is checked when you run research" in RESEARCH
    assert "Optional diagnostic check has not been run on this page load" in RESEARCH
    assert 'id="labLegacyDiagnostics"' in RESEARCH
    assert "event.target.open)refresh()" in RESEARCH
    assert "refreshFyersConnection();\\nrefresh();" not in RESEARCH
    assert "Server receipt time only" in RESEARCH
