"""SQLite-Schicht: Rankings, virtuelles Depot, Scan-Logs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from config import DATABASE_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                message TEXT,
                scanned INTEGER DEFAULT 0,
                passed_koenig INTEGER DEFAULT 0,
                passed_trend INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                duration_sec REAL
            );

            CREATE TABLE IF NOT EXISTS rankings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                rank INTEGER,
                ticker TEXT NOT NULL,
                name TEXT,
                price REAL,
                sma200 REAL,
                rsl REAL,
                trailing_pe REAL,
                avg_pe_5y REAL,
                gross_margin REAL,
                roe REAL,
                debt_to_equity REAL,
                above_sma200 INTEGER,
                passed_koenig INTEGER,
                passed_trend INTEGER,
                reject_reason TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (scan_id) REFERENCES scan_runs(id)
            );

            CREATE TABLE IF NOT EXISTS portfolio (
                ticker TEXT PRIMARY KEY,
                name TEXT,
                buy_date TEXT NOT NULL,
                buy_price REAL NOT NULL,
                last_price REAL,
                last_rsl REAL,
                last_rank INTEGER,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                message TEXT NOT NULL,
                rsl REAL,
                rank INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_rankings_scan ON rankings(scan_id);
            CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
            """
        )


def start_scan_run() -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scan_runs (started_at, status, message) VALUES (?, 'running', ?)",
            (utc_now_iso(), "Scan gestartet"),
        )
        return int(cur.lastrowid)


def finish_scan_run(
    scan_id: int,
    status: str,
    message: str,
    scanned: int,
    passed_koenig: int,
    passed_trend: int,
    errors: int,
    duration_sec: float,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE scan_runs
               SET finished_at = ?, status = ?, message = ?,
                   scanned = ?, passed_koenig = ?, passed_trend = ?,
                   errors = ?, duration_sec = ?
             WHERE id = ?
            """,
            (
                utc_now_iso(),
                status,
                message,
                scanned,
                passed_koenig,
                passed_trend,
                errors,
                duration_sec,
                scan_id,
            ),
        )


def replace_rankings(scan_id: int, rows: list[dict[str, Any]]) -> None:
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute("DELETE FROM rankings WHERE scan_id != ?", (scan_id,))
        for row in rows:
            conn.execute(
                """
                INSERT INTO rankings (
                    scan_id, rank, ticker, name, price, sma200, rsl,
                    trailing_pe, avg_pe_5y, gross_margin, roe, debt_to_equity,
                    above_sma200, passed_koenig, passed_trend, reject_reason, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    row.get("rank"),
                    row["ticker"],
                    row.get("name"),
                    row.get("price"),
                    row.get("sma200"),
                    row.get("rsl"),
                    row.get("trailing_pe"),
                    row.get("avg_pe_5y"),
                    row.get("gross_margin"),
                    row.get("roe"),
                    row.get("debt_to_equity"),
                    1 if row.get("above_sma200") else 0,
                    1 if row.get("passed_koenig") else 0,
                    1 if row.get("passed_trend") else 0,
                    row.get("reject_reason"),
                    now,
                ),
            )


def latest_scan() -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def latest_finished_scan() -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM scan_runs
             WHERE status IN ('ok', 'error')
             ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None


def rankings_for_scan(scan_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM rankings
             WHERE scan_id = ?
             ORDER BY CASE WHEN rank IS NULL THEN 9999 ELSE rank END, ticker
            """,
            (scan_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def latest_ranked_universe() -> list[dict[str, Any]]:
    scan = latest_finished_scan()
    if not scan:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM rankings
             WHERE scan_id = ? AND passed_trend = 1 AND rank IS NOT NULL
             ORDER BY rank ASC
            """,
            (scan["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


def get_portfolio() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio ORDER BY buy_date ASC, ticker"
        ).fetchall()
        return [dict(r) for r in rows]


def portfolio_tickers() -> set[str]:
    return {p["ticker"] for p in get_portfolio()}


def add_to_portfolio(ticker: str, name: str | None, price: float, rsl: float | None, rank: int | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO portfolio
                (ticker, name, buy_date, buy_price, last_price, last_rsl, last_rank, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, name, utc_now_iso(), price, price, rsl, rank, "Auto-Kaufsignal"),
        )


def remove_from_portfolio(ticker: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM portfolio WHERE ticker = ?", (ticker,))


def update_portfolio_mark(ticker: str, price: float | None, rsl: float | None, rank: int | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE portfolio
               SET last_price = ?, last_rsl = ?, last_rank = ?
             WHERE ticker = ?
            """,
            (price, rsl, rank, ticker),
        )


def add_signal(kind: str, ticker: str, name: str | None, message: str, rsl: float | None, rank: int | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO signals (created_at, kind, ticker, name, message, rsl, rank)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (utc_now_iso(), kind, ticker, name, message, rsl, rank),
        )


def recent_signals(limit: int = 30) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def recent_scans(limit: int = 15) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
