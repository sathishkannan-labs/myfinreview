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
    assert "async function analyzeHistoricalFile(file,synthetic=false)" in RESEARCH
    assert "analyzeHistoricalFile(file);" in RESEARCH
    assert "data.strategies?.breakout?.observation" in RESEARCH
    assert "data.strategies?.sma_trend?.observation" in RESEARCH
    assert "id=\"labOverview\"" in RESEARCH
    assert "Historical data is over 7 days old" in RESEARCH
    assert 'id="downloadResearchReport"' in RESEARCH
    assert "lastReport=makeExportReport(data,sourceMeta)" in RESEARCH


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
    assert RESEARCH.index('id="csvResearch"') < RESEARCH.index('id="labReport"') < RESEARCH.index('id="labOptional"')
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


def test_export_provenance_and_research_button_behavior():
    import shutil
    import subprocess
    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    export = RESEARCH.split("function makeExportReport", 1)[1].split("function renderResearchReport", 1)[0]
    gate = RESEARCH.split("function updateFyersResearchButton", 1)[1].split("function setFyersSessionDeadline", 1)[0]
    code = "function makeExportReport" + export + "function updateFyersResearchButton" + gate + """
const assert=require('node:assert/strict');
const raw={source:'USER_UPLOADED_CSV',first_timestamp:'start',last_timestamp:'end',
           adjustment:'UNVERIFIED',exchange_calendar:'NOT_VERIFIED',last_close:42};
const sample=makeExportReport(raw,{label:'demo.csv',synthetic:true,provider:'csv'});
assert.equal(sample.provenance.provider,'FINREVIEW_SYNTHETIC_SAMPLE');
assert.equal(sample.provenance.synthetic,true);
assert.equal(sample.provenance.filename,'demo.csv');
assert.deepEqual(sample.provenance.observation_period,{start:'start',end:'end'});
assert.ok(Number.isFinite(Date.parse(sample.provenance.generated_at)));
assert.equal(sample.provenance.limitations.adjustment,'UNVERIFIED');
assert.equal(sample.last_close,42);
assert.equal(raw.provenance,undefined);
const upload=makeExportReport(raw,{label:'synthetic-named-upload.csv',synthetic:false,provider:'csv'});
assert.equal(upload.provenance.synthetic,false);
const broker=makeExportReport({...raw,data_rights:'PERSONAL_ONLY_NOT_FOR_REDISTRIBUTION'},
                              {label:'NSE:INFY-EQ',synthetic:false,provider:'fyers'});
assert.equal(broker.provenance.instrument,'NSE:INFY-EQ');
assert.equal(broker.provenance.filename,null);
assert.equal(broker.provenance.limitations.data_rights,'PERSONAL_ONLY_NOT_FOR_REDISTRIBUTION');
let fyersTestAvailable=true, fyersSessionDeadline=0, fyersResearchPending=false;
const fyersSelected={value:'NSE:INFY-EQ'}, button={};
const document={querySelector:()=>button};
updateFyersResearchButton();
assert.equal(button.disabled,true);
fyersSessionDeadline=Date.now()+60000;
updateFyersResearchButton();
assert.equal(button.disabled,false);
fyersSelected.value='';
updateFyersResearchButton();
assert.equal(button.disabled,true);
fyersSelected.value='NSE:INFY-EQ';
fyersResearchPending=true;
updateFyersResearchButton();
assert.equal(button.disabled,true);
"""
    result = subprocess.run([node, "-e", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
