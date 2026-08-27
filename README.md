# Königs-Trend-Screener für Raspberry Pi

Leichtgewichtige Flask-App mit SQLite. Täglich nach US-Börsenschluss:

1. **Königsanalyse** (Max Otte): Bruttomarge > 35 %, ROE > 15 %, D/E < 1,5, KGV unter 5-Jahres-Schnitt (sonst KGV < 28).
2. **BOTSI-Trendmonitor**: Kurs über GD200, Ranking nach Relativer Stärke Levy (RSL-135).
3. **Signale**: Kauf bei neuem Top-10-Einstieg, Verkauf bei GD200-Bruch oder Fall aus den Top 15.

Keine Anlageberatung. Datenquelle ist Yahoo Finance über `yfinance` und kann lückenhaft sein.

## Dateien

```
aktien-screener/
├── app.py                 # Flask, Scheduler, Signal-Logik
├── screener.py            # Fundamentale + Timing-Filter
├── notifier.py            # Telegram / Discord
├── database.py            # SQLite
├── config.py              # Watchlist und Schwellwerte
├── templates/index.html   # Dashboard
├── requirements.txt
├── .env.example
└── systemd/aktien-screener.service
```

Watchlist und Filtergrenzen stehen in `config.py` bzw. in der `.env`.

## Installation auf dem Raspberry Pi

Voraussetzung: Raspberry Pi OS Bookworm (64-bit empfohlen), Python 3.11+.

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git

cd ~
# Projektordner hierher kopieren, danach:
cd ~/aktien-screener

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
nano .env
```

### Telegram einrichten

1. In Telegram `@BotFather` öffnen, `/newbot` ausführen, Token kopieren.
2. Den Bot anschreiben.
3. Chat-ID ermitteln, z. B. mit `@userinfobot` oder:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. `TELEGRAM_BOT_TOKEN` und `TELEGRAM_CHAT_ID` in `.env` eintragen.

Discord: Incoming Webhook in einem Channel erstellen und `DISCORD_WEBHOOK_URL` setzen.

### Manuell testen

```bash
source .venv/bin/activate
python app.py
```

Dashboard: `http://<IP-des-Pi>:8080`  
Ersten Scan über den Button im Interface auslösen (ca. 2–4 Minuten für ~40 Titel).

## Als systemd-Service (startet nach jedem Reboot)

Pfad und User in `systemd/aktien-screener.service` anpassen, falls der Ordner nicht `/home/pi/aktien-screener` ist.

```bash
sudo cp ~/aktien-screener/systemd/aktien-screener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aktien-screener
sudo systemctl status aktien-screener
```

Logs:

```bash
journalctl -u aktien-screener -f
tail -f ~/aktien-screener/data/screener.log
```

Dienst neu starten nach Code- oder `.env`-Änderung:

```bash
sudo systemctl restart aktien-screener
```

## Zeitplan

Standard: **22:30 Europe/Berlin**. Über `.env` änderbar:

```
SCAN_TIMEZONE=Europe/Berlin
SCAN_HOUR=22
SCAN_MINUTE=30
```

Ein zusätzlicher Cron-Job ist nicht nötig. APScheduler läuft im Prozess.

## Robustheit

- Jede Aktie in eigenem try/except.
- Kurze Pause zwischen yfinance-Requests.
- Fehlende Kennzahlen = Filter nicht bestanden (kein stilles Durchwinken).
- Debt/Equity und Margen werden automatisch von Prozent- in Dezimalform normiert.
- 5-Jahres-KGV best-effort aus Jahres-EPS und Jahresschlusskurs; sonst KGV < 28.

## Hinweis zum Pi

Nicht parallel mit schweren Desktop-Sessions scannen. 400 MB MemoryMax im Unit-File begrenzen Ausreißer. Bei häufigen yfinance-Timeouts `REQUEST_PAUSE_SEC` in `config.py` leicht erhöhen (z. B. 0.8).
