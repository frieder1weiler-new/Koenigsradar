"""Zentrale Konfiguration. Secrets kommen aus Umgebungsvariablen oder .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- Pfade ---
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DATABASE_PATH = DATA_DIR / "screener.db"

# --- Web ---
SECRET_KEY = os.getenv("SECRET_KEY", "bitte-in-produktion-aendern")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
DEBUG = os.getenv("DEBUG", "0") == "1"

# --- Scheduler (nach US-Börsenschluss, mit Puffer) ---
SCAN_TIMEZONE = os.getenv("SCAN_TIMEZONE", "Europe/Berlin")
SCAN_HOUR = int(os.getenv("SCAN_HOUR", "22"))
SCAN_MINUTE = int(os.getenv("SCAN_MINUTE", "30"))

# --- Königsanalyse (Prof. Max Otte) ---
MIN_GROSS_MARGIN = float(os.getenv("MIN_GROSS_MARGIN", "0.35"))  # 35 %
MIN_ROE = float(os.getenv("MIN_ROE", "0.15"))  # 15 %
MAX_DEBT_TO_EQUITY = float(os.getenv("MAX_DEBT_TO_EQUITY", "1.5"))
MAX_PE_FALLBACK = float(os.getenv("MAX_PE_FALLBACK", "28"))
MIN_PE_YEARS = int(os.getenv("MIN_PE_YEARS", "2"))

# --- BOTSI-Trendmonitor ---
SMA_DAYS = int(os.getenv("SMA_DAYS", "200"))
RSL_DAYS = int(os.getenv("RSL_DAYS", "135"))
TOP_N_BUY = int(os.getenv("TOP_N_BUY", "10"))
TOP_N_HOLD = int(os.getenv("TOP_N_HOLD", "15"))

# Pause zwischen yfinance-Requests (Pi + Rate-Limits)
REQUEST_PAUSE_SEC = float(os.getenv("REQUEST_PAUSE_SEC", "0.45"))

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# --- Discord (optional, alternativ oder zusätzlich) ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# Globale Burggraben-/Qualitätswatchlist (~40 Titel)
# yfinance-Symbole: US ohne Suffix, Europa mit Börsenkürzel.
WATCHLIST = [
    # US Tech / Plattformen
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "AVGO",
    "ORCL",
    "ADBE",
    "CRM",
    "INTU",
    "NOW",
    # Halbleiter / Ausrüstung
    "TSM",
    "ASML",
    "AMAT",
    # Zahlungsverkehr / Rating / Finanzinfra
    "V",
    "MA",
    "SPGI",
    "MCO",
    "BLK",
    "BRK-B",
    # Konsum / staples
    "KO",
    "PEP",
    "PG",
    "COST",
    "WMT",
    "MCD",
    "NKE",
    "DIS",
    "HD",
    # Healthcare
    "JNJ",
    "UNH",
    "LLY",
    "ABBV",
    "NVO",
    # Europa
    "MC.PA",  # LVMH
    "OR.PA",  # L'Oréal
    "NESN.SW",  # Nestlé
    "NOVN.SW",  # Novartis
    "ROG.SW",  # Roche
    "SAP.DE",
    "SIE.DE",
    "ULVR.L",  # Unilever
    "REL.L",  # RELX
    "AZN.L",
]


def unique_watchlist() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in WATCHLIST:
        key = t.upper()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out
