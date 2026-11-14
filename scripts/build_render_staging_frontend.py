"""Build static staging frontend with a validated public API origin.

Only public configuration is embedded. No broker or application secrets are
read or copied into the HTML/JS bundle.
"""
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend"
TARGET = ROOT / "staging-dist"


def validated_api_url(raw: str) -> str:
    parsed = urlsplit(raw.strip())
    if (parsed.scheme != "https" or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or
            parsed.fragment or parsed.path not in ("", "/") or
            not parsed.hostname.endswith(".onrender.com")):
        raise ValueError("STAGING_API_URL must be an HTTPS onrender.com API origin")
    return parsed.geturl().rstrip("/")


def build(raw_url: str):
    url = validated_api_url(raw_url)
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    runtime = 'window.FINREVIEW_API_URL = ' + json.dumps(url) + ';\n'
    (TARGET / "config.runtime.js").write_text(runtime, encoding="utf-8")
    for page in TARGET.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        html = html.replace('src="config.runtime.php"', 'src="config.runtime.js"')
        # Research preview doesn't load config.runtime.php in source.
        if 'config.runtime.js' not in html:
            html = html.replace('src="./config.js"',
                                'src="./config.runtime.js"></script><script src="./config.js"')
            html = html.replace('src="config.js"',
                                'src="config.runtime.js"></script><script src="config.js"')
        page.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    build(os.environ.get("STAGING_API_URL", ""))
