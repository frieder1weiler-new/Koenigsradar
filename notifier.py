"""Kostenlose Push-Kanäle: Telegram Bot API und/oder Discord Webhook."""

from __future__ import annotations

import logging

import requests

from config import DISCORD_WEBHOOK_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger("screener.notify")


def _send_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            log.warning("Telegram HTTP %s: %s", resp.status_code, resp.text[:300])
            return False
        return True
    except Exception as exc:
        log.warning("Telegram fehlgeschlagen: %s", exc)
        return False


def _send_discord(text: str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": text[:1900]},
            timeout=20,
        )
        if resp.status_code >= 400:
            log.warning("Discord HTTP %s: %s", resp.status_code, resp.text[:300])
            return False
        return True
    except Exception as exc:
        log.warning("Discord fehlgeschlagen: %s", exc)
        return False


def notify(text: str) -> bool:
    """Sendet an alle konfigurierten Kanäle. True, wenn mindestens einer klappt."""
    sent = False
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        sent = _send_telegram(text) or sent
    if DISCORD_WEBHOOK_URL:
        sent = _send_discord(text) or sent
    if not TELEGRAM_BOT_TOKEN and not DISCORD_WEBHOOK_URL:
        log.info("Kein Benachrichtigungskanal konfiguriert. Meldung: %s", text)
    return sent


def configured() -> bool:
    return bool((TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) or DISCORD_WEBHOOK_URL)
