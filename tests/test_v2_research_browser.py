"""Chromium acceptance for the Community Research Lab with disposable local data."""
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = "http://localhost:8080"
API = "http://localhost:8000"


@pytest.mark.skipif(os.getenv("FINREVIEW_TEST_BROWSER") != "1", reason="requires Chromium acceptance environment")
def test_research_file_upload_export_and_print(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    pdf_reader = pytest.importorskip("pypdf").PdfReader
    env = dict(
        os.environ,
        DATABASE_URL=f"sqlite:///{tmp_path / 'finreview-browser.db'}",
        AUTH_SECRET_KEY=secrets.token_urlsafe(48),
        CORS_ALLOW_ORIGINS=FRONTEND,
        FINREVIEW_V2_OAUTH_ENABLED="false",
        FINREVIEW_V2_BROKER_PROBES_ENABLED="false",
    )
    with subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT / "backend", env=env,
    ) as backend, subprocess.Popen(
        [sys.executable, "-m", "http.server", "8080", "--bind", "127.0.0.1"],
        cwd=ROOT / "frontend",
    ) as frontend:
        try:
            with httpx.Client(trust_env=False, timeout=2) as client:
                for _ in range(60):
                    assert backend.poll() is None and frontend.poll() is None
                    try:
                        if client.get(API).is_success and client.get(FRONTEND).is_success:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.25)
                else:
                    pytest.fail("Disposable FinReview servers did not start")
                account = {"email": "browser-qa@example.com", "password": secrets.token_urlsafe(24)}
                client.post(f"{API}/auth/signup", json=account).raise_for_status()
                login = client.post(f"{API}/auth/login", json=account).raise_for_status().json()

            with playwright.sync_playwright() as runtime:
                browser = runtime.chromium.launch(executable_path=os.environ.get("FINREVIEW_TEST_CHROMIUM"))
                context = browser.new_context(accept_downloads=True, viewport={"width": 390, "height": 844})
                context.add_init_script(
                    "if (location.origin === " + json.dumps(FRONTEND) + ") "
                    "localStorage.setItem('finreview_token', " + json.dumps(login["token"]) + ");"
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # The Lab uses Bootstrap's stylesheet. The application logic and HTTP API run unmocked.
                page.goto(f"{FRONTEND}/intelligence.html", wait_until="domcontentloaded")
                expect = playwright.expect
                expect(page.locator("#liveBrokerResearch")).to_be_hidden()

                file = page.locator("#researchCsvInput")
                file.set_input_files(ROOT / "frontend/data/research-sample-synthetic.csv")
                page.locator("#runCsvResearch").click()
                expect(page.locator("#labOverview")).to_contain_text("User-supplied CSV")
                expect(page.locator("#labOverview")).to_contain_text("65 historical candles")

                file.set_input_files({"name": "invalid.csv", "mimeType": "text/csv", "buffer": b"date,close\n2025-01-01,1"})
                page.locator("#runCsvResearch").click()
                expect(page.locator("#csvResearchNotice")).to_contain_text("CSV headers")
                expect(page.locator("#labReport")).to_be_hidden()

                page.get_by_role("button", name="Try sample").click()
                expect(page.locator("#labOverview")).to_contain_text("SYNTHETIC SAMPLE")
                with page.expect_download() as saved:
                    page.get_by_role("button", name="Download JSON").click()
                artifact = saved.value
                assert artifact.suggested_filename.endswith(".json")
                report = json.loads(artifact.path().read_text(encoding="utf-8"))
                provenance = report["provenance"]
                assert report["rows"] == 65
                assert provenance["provider"] == "FINREVIEW_SYNTHETIC_SAMPLE"
                assert provenance["synthetic"] is True
                assert provenance["observation_period"]["start"] < provenance["observation_period"]["end"]

                details = page.locator("#reportAnalysisDetails")
                assert details.get_attribute("open") is None
                document = pdf_reader(io.BytesIO(page.pdf()))
                printed = " ".join(" ".join(p.extract_text().split()) for p in document.pages)
                assert "SYNTHETIC SAMPLE" in printed
                assert "20-bar breakout" in printed
                assert "20/50-day moving average" in printed
                assert details.get_attribute("open") is None
                assert page.locator("html").evaluate("(el) => el.scrollWidth <= innerWidth")
                assert not errors, errors
                browser.close()
        finally:
            backend.terminate()
            frontend.terminate()
