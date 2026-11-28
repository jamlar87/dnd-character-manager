from __future__ import annotations
from pathlib import Path
import json
import os
import urllib.request


# Telegram notification (loaded from ~/.hermes/.env if not already exported)
def _load_telegram_env():
    """Load TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from env or ~/.hermes/.env."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        return token, chat_id
    # Fallback: parse from Hermes .env file
    env_file = Path(os.path.expanduser("~/.hermes/.env"))
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key == "TELEGRAM_BOT_TOKEN" and not token:
                token = val
            elif key == "TELEGRAM_CHAT_ID" and not chat_id:
                chat_id = val
    return token, chat_id

TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID = _load_telegram_env()

# ═══════════════════════════════════════════════════════════════════════════════
# Telegram notification
# ═══════════════════════════════════════════════════════════════════════════════

def _notify_telegram(text: str):
    """Send a Telegram message via Bot API. Logs failure but never blocks extraction."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  (Telegram notify: not configured, skipping)")
        return
    # Trim to Telegram's 4096 char limit
    if len(text) > 4000:
        text = text[:4000] + "\n...\n(truncated)"
    try:
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if not result.get("ok"):
            # If HTML parse fails, retry without parse_mode
            if "can't parse entities" in str(result).lower():
                data2 = json.dumps({
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "disable_web_page_preview": True,
                }).encode()
                req2 = urllib.request.Request(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data=data2,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req2, timeout=10)
    except Exception as e:
        print(f"  (Telegram notify failed: {e})")


def _send_readout(text: str):
    """Print to stdout AND send to Telegram if configured."""
    print(text)
    # Strip box-drawing chars for Telegram (they render poorly)
    clean = text.replace("┌─", "──").replace("─┐", "──").replace("├─", "──") \
                .replace("─┤", "──").replace("└─", "──").replace("─┘", "──") \
                .replace("│", " ").replace("⚠", "⚠️")
    _notify_telegram(clean)
