#!/usr/bin/env python3
"""Königs-Trend-Screener: Flask-Dashboard + täglicher Hintergrundscan."""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

import config
import database as db
import notifier
from screener import qualified_ranked, scan_watchlist

BASE_DIR = Path(__file__).resolve().parent
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "data" / "screener.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("screener.app")

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

_scan_lock = threading.Lock()
_scheduler: BackgroundScheduler | None = None


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value * 100:.1f} %"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "–"
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_ts(value: str | None) -> str:
    if not value:
        return "noch nie"
    try:
        raw = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ZoneInfo(config.SCAN_TIMEZONE)).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return value


app.jinja_env.filters["pct"] = _fmt_pct
app.jinja_env.filters["num"] = _fmt_num
app.jinja_env.filters["ts"] = _fmt_ts


def run_daily_scan(manual: bool = False) -> dict:
    """Vollständiger Tageslauf inkl. Signalen und Depot-Update."""
    if not _scan_lock.acquire(blocking=False):
        return {"ok": False, "message": "Ein Scan läuft bereits."}

    started = time.monotonic()
    scan_id = db.start_scan_run()
    log.info("Scan #%s gestartet (manual=%s)", scan_id, manual)
    try:
        rows = scan_watchlist()
        ranked = qualified_ranked(rows)
        db.replace_rankings(scan_id, rows)

        top10 = {r["ticker"]: r for r in ranked if r["rank"] <= config.TOP_N_BUY}
        top15 = {r["ticker"]: r for r in ranked if r["rank"] <= config.TOP_N_HOLD}
        by_ticker = {r["ticker"]: r for r in rows}

        held = db.get_portfolio()
        held_set = {p["ticker"] for p in held}
        buy_signals = 0
        sell_signals = 0

        for ticker, row in top10.items():
            if ticker in held_set:
                db.update_portfolio_mark(ticker, row.get("price"), row.get("rsl"), row.get("rank"))
                continue
            price = row.get("price") or 0.0
            rsl = row.get("rsl")
            rank = row.get("rank")
            name = row.get("name") or ticker
            msg = (
                f"KAUFSIGNAL: {name} ({ticker}) hat die Königsanalyse bestanden "
                f"und erreicht RSL-Score {_fmt_num(rsl, 3)} (Rang {rank})."
            )
            db.add_to_portfolio(ticker, name, price, rsl, rank)
            db.add_signal("buy", ticker, name, msg, rsl, rank)
            notifier.notify(msg)
            buy_signals += 1
            log.info(msg)

        for pos in held:
            ticker = pos["ticker"]
            row = by_ticker.get(ticker)
            name = (row or {}).get("name") or pos.get("name") or ticker
            if row:
                db.update_portfolio_mark(ticker, row.get("price"), row.get("rsl"), row.get("rank"))

            reasons: list[str] = []
            if row is None or row.get("error"):
                # Kein Zwangsverkauf nur wegen eines API-Aussetzers
                log.warning("Keine verlässlichen Daten für Depot-Titel %s – Position bleibt", ticker)
                continue
            else:
                if not row.get("above_sma200"):
                    reasons.append("GD200 verletzt")
                rank = row.get("rank")
                if ticker not in top15:
                    reasons.append("Momentum verloren")
                # Zusätzlich explizit, falls rang > 15 aber passed_trend
                if rank is not None and rank > config.TOP_N_HOLD and "Momentum verloren" not in reasons:
                    reasons.append("Momentum verloren")

            if not reasons:
                continue

            grund = " / ".join(reasons)
            msg = f"VERKAUFSIGNAL: {name} ({ticker}) aussteigen (Grund: {grund})."
            db.remove_from_portfolio(ticker)
            db.add_signal("sell", ticker, name, msg, (row or {}).get("rsl"), (row or {}).get("rank"))
            notifier.notify(msg)
            sell_signals += 1
            log.info(msg)

        duration = time.monotonic() - started
        errors = sum(1 for r in rows if r.get("error"))
        passed_k = sum(1 for r in rows if r.get("passed_koenig"))
        passed_t = sum(1 for r in rows if r.get("passed_trend"))
        message = (
            f"{'Manueller' if manual else 'Automatischer'} Scan fertig. "
            f"{len(rows)} Titel, {passed_k} Königsfilter, {passed_t} Trendfilter, "
            f"{buy_signals} Kauf-, {sell_signals} Verkaufssignale, {errors} Fehler."
        )
        db.finish_scan_run(
            scan_id,
            "ok",
            message,
            len(rows),
            passed_k,
            passed_t,
            errors,
            duration,
        )
        summary = (
            f"Scan abgeschlossen: {passed_t} Titel im Universum, "
            f"{buy_signals} Kauf / {sell_signals} Verkauf."
        )
        if manual is False:
            notifier.notify(summary)
        log.info(message)
        return {"ok": True, "message": message, "scan_id": scan_id}
    except Exception as exc:
        duration = time.monotonic() - started
        log.exception("Scan fehlgeschlagen")
        db.finish_scan_run(scan_id, "error", str(exc), 0, 0, 0, 1, duration)
        notifier.notify(f"Scan-Fehler: {exc}")
        return {"ok": False, "message": str(exc), "scan_id": scan_id}
    finally:
        _scan_lock.release()


def _dashboard_context() -> dict:
    scan = db.latest_scan()
    finished = db.latest_finished_scan()
    rankings = db.latest_ranked_universe()
    top10 = rankings[: config.TOP_N_BUY]
    portfolio = db.get_portfolio()
    rank_map = {r["ticker"]: r for r in rankings}

    enriched_portfolio = []
    for pos in portfolio:
        live = rank_map.get(pos["ticker"])
        buy = pos.get("buy_price") or 0
        last = (live or {}).get("price") or pos.get("last_price")
        pnl = None
        pnl_pct = None
        if buy and last:
            pnl = last - buy
            pnl_pct = (last / buy) - 1
        enriched_portfolio.append(
            {
                **pos,
                "live": live,
                "last": last,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "rank": (live or {}).get("rank") or pos.get("last_rank"),
                "rsl": (live or {}).get("rsl") or pos.get("last_rsl"),
                "above_sma200": (live or {}).get("above_sma200", 1),
            }
        )

    next_run = None
    if _scheduler:
        job = _scheduler.get_job("daily_scan")
        if job and job.next_run_time:
            next_run = job.next_run_time.astimezone(ZoneInfo(config.SCAN_TIMEZONE)).strftime(
                "%d.%m.%Y %H:%M %Z"
            )

    return {
        "top10": top10,
        "rankings": rankings,
        "portfolio": enriched_portfolio,
        "logs": db.recent_scans(12),
        "signals": db.recent_signals(20),
        "scan": scan,
        "finished": finished,
        "next_run": next_run,
        "notify_ok": notifier.configured(),
        "watchlist_n": len(config.unique_watchlist()),
        "top_n_buy": config.TOP_N_BUY,
        "top_n_hold": config.TOP_N_HOLD,
        "scan_clock": f"{config.SCAN_HOUR:02d}:{config.SCAN_MINUTE:02d}",
        "tz": config.SCAN_TIMEZONE,
        "scan_running": _scan_lock.locked(),
    }


@app.route("/")
def index():
    return render_template("index.html", **_dashboard_context())


@app.post("/scan")
def trigger_scan():
    if _scan_lock.locked():
        flash("Ein Scan läuft bereits.", "warn")
        return redirect(url_for("index"))
    thread = threading.Thread(target=run_daily_scan, kwargs={"manual": True}, daemon=True)
    thread.start()
    flash("Manueller Scan gestartet. Seite in ein bis drei Minuten aktualisieren.", "ok")
    return redirect(url_for("index"))


@app.get("/api/status")
def api_status():
    scan = db.latest_scan()
    return jsonify({"running": _scan_lock.locked(), "latest": scan})


@app.get("/api/rankings")
def api_rankings():
    return jsonify(db.latest_ranked_universe())


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=config.SCAN_TIMEZONE)
    scheduler.add_job(
        run_daily_scan,
        CronTrigger(
            hour=config.SCAN_HOUR,
            minute=config.SCAN_MINUTE,
            timezone=config.SCAN_TIMEZONE,
        ),
        id="daily_scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    log.info(
        "Scheduler aktiv: täglich %02d:%02d %s",
        config.SCAN_HOUR,
        config.SCAN_MINUTE,
        config.SCAN_TIMEZONE,
    )
    return scheduler


def create_app() -> Flask:
    db.init_db()
    return app


# Gunicorn: app:app – Scheduler über --preload vermeiden, daher lazy start
_booted = False
_boot_lock = threading.Lock()


@app.before_request
def _ensure_runtime():
    global _scheduler, _booted
    with _boot_lock:
        if _booted:
            return
        db.init_db()
        if _scheduler is None:
            _scheduler = start_scheduler()
        _booted = True


if __name__ == "__main__":
    db.init_db()
    _scheduler = start_scheduler()
    log.info("Webinterface auf http://%s:%s", config.HOST, config.PORT)
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, use_reloader=False)
