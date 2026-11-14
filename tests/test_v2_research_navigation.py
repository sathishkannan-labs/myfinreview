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
    assert "Live broker authentication" in RESEARCH


def test_research_workspace_uses_existing_auth_and_apis():
    assert "finreview_token" in RESEARCH
    assert "/v2/signals" in RESEARCH
    assert "/v2/paper-account" in RESEARCH
    assert "/v2/market/preference" in RESEARCH


def test_research_workflow_is_primary_and_experimental_controls_collapsed():
    assert 'id="labReport"' in RESEARCH
    assert 'id="labMetrics"' in RESEARCH
    assert 'id="downloadResearchReport"' in RESEARCH
    assert 'id="labOptional"' in RESEARCH
    assert RESEARCH.index('id="researchCsvForm"') < RESEARCH.index('id="labOptional"')
    assert "Historical research report" in RESEARCH
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
    scripts = re.findall(r"<script(?:\\s[^>]*)?>([\\s\\S]*?)</script>", RESEARCH)
    assert scripts
    js = tmp_path / "research-lab.js"
    js.write_text("\\n".join(scripts), encoding="utf-8")
    result = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
