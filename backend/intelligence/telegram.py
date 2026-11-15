"""Private Telegram delivery; no trading capability, no credentials in messages."""
import os
import httpx

async def send_private_report(message: str, *, client=None) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or token == "YOUR_BOT_TOKEN" or not chat_id or chat_id == "YOUR_CHAT_ID":
        raise RuntimeError("Telegram is not configured")
    if not message or len(message) > 4096:
        raise ValueError("Telegram report must be 1..4096 characters")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "disable_web_page_preview": True}
    if client is None:
        async with httpx.AsyncClient(timeout=12) as session:
            response = await session.post(url, json=payload)
    else:
        response = await client.post(url, json=payload)
    response.raise_for_status()
    result = response.json()
    if result.get("ok") is not True:
        raise RuntimeError("Telegram rejected report")
    return True
