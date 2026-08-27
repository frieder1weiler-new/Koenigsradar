"""Königsanalyse (Max Otte) + BOTSI-Trendmonitor (GD200 + RSL-135)."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import pandas as pd
import yfinance as yf

from config import (
    MAX_DEBT_TO_EQUITY,
    MAX_PE_FALLBACK,
    MIN_GROSS_MARGIN,
    MIN_PE_YEARS,
    MIN_ROE,
    REQUEST_PAUSE_SEC,
    RSL_DAYS,
    SMA_DAYS,
    unique_watchlist,
)

log = logging.getLogger("screener.core")


def _finite(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return None
        return num
    except (TypeError, ValueError):
        return None


def _as_ratio(value: Any) -> float | None:
    """yfinance liefert Kennzahlen mal als 0.35, mal als 35.0."""
    num = _finite(value)
    if num is None:
        return None
    if abs(num) > 5:
        return num / 100.0
    return num


def _as_leverage(value: Any) -> float | None:
    """Debt/Equity: Yahoo oft in Prozent (z. B. 172), gelegentlich als 1.72."""
    num = _finite(value)
    if num is None:
        return None
    if abs(num) > 10:
        return num / 100.0
    return num


def _pick_info(info: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in info and info[key] not in (None, "", "None"):
            return info[key]
    return None


def _frame(ticker: yf.Ticker, *attrs: str) -> pd.DataFrame | None:
    for attr in attrs:
        try:
            frame = getattr(ticker, attr)
            if frame is not None and not getattr(frame, "empty", True):
                return frame
        except Exception:
            continue
    return None


def _row(frame: pd.DataFrame | None, *labels: str) -> pd.Series | None:
    if frame is None:
        return None
    for label in labels:
        if label in frame.index:
            return frame.loc[label]
    lowered = {str(idx).strip().lower(): idx for idx in frame.index}
    for label in labels:
        idx = lowered.get(label.lower())
        if idx is not None:
            return frame.loc[idx]
    return None


def _latest_positive(series: pd.Series | None) -> float | None:
    if series is None:
        return None
    for value in series.tolist():
        num = _finite(value)
        if num is not None:
            return num
    return None


def _ttm_sum(quarterly: pd.DataFrame | None, *labels: str) -> float | None:
    series = _row(quarterly, *labels)
    if series is None:
        return None
    vals = [_finite(v) for v in series.tolist()[:4]]
    vals = [v for v in vals if v is not None]
    if len(vals) < 4:
        return None
    return sum(vals)


def fundamentals_from_statements(ticker: yf.Ticker) -> dict[str, float | None]:
    """Fallback, wenn ticker.info durch Yahoo blockiert wird."""
    out: dict[str, float | None] = {
        "gross_margin": None,
        "roe": None,
        "debt_to_equity": None,
        "trailing_eps": None,
    }
    annual = _frame(ticker, "income_stmt", "financials")
    quarterly = _frame(ticker, "quarterly_income_stmt", "quarterly_financials")
    balance = _frame(ticker, "balance_sheet", "quarterly_balance_sheet")

    revenue = _ttm_sum(quarterly, "Total Revenue", "Operating Revenue")
    gross = _ttm_sum(quarterly, "Gross Profit")
    net = _ttm_sum(quarterly, "Net Income", "Net Income Common Stockholders")
    if revenue is None:
        revenue = _latest_positive(_row(annual, "Total Revenue", "Operating Revenue"))
    if gross is None:
        gross = _latest_positive(_row(annual, "Gross Profit"))
    if net is None:
        net = _latest_positive(_row(annual, "Net Income", "Net Income Common Stockholders"))

    if revenue and revenue != 0 and gross is not None:
        out["gross_margin"] = gross / revenue

    equity = _latest_positive(
        _row(
            balance,
            "Stockholders Equity",
            "Common Stock Equity",
            "Total Equity Gross Minority Interest",
        )
    )
    total_debt = _latest_positive(_row(balance, "Total Debt"))
    if total_debt is None:
        long_d = _latest_positive(_row(balance, "Long Term Debt", "Long Term Debt And Capital Lease Obligation")) or 0.0
        curr_d = _latest_positive(_row(balance, "Current Debt", "Current Debt And Capital Lease Obligation")) or 0.0
        if long_d or curr_d:
            total_debt = long_d + curr_d

    if equity and equity != 0 and net is not None:
        out["roe"] = net / equity
    if equity and equity != 0 and total_debt is not None:
        out["debt_to_equity"] = total_debt / equity

    eps = _ttm_sum(quarterly, "Diluted EPS", "Basic EPS")
    if eps is None:
        eps = _latest_positive(_row(annual, "Diluted EPS", "Basic EPS"))
    out["trailing_eps"] = eps
    return out


def _compute_avg_pe_5y(ticker: yf.Ticker, hist: pd.DataFrame) -> float | None:
    """Best-effort 5-Jahres-Durchschnitts-KGV aus Jahresabschluss + Jahresschlusskurs."""
    stmt = None
    for getter in ("income_stmt", "financials"):
        try:
            candidate = getattr(ticker, getter)
            if candidate is not None and not getattr(candidate, "empty", True):
                stmt = candidate
                break
        except Exception:
            continue
    if stmt is None:
        return None

    eps_row = None
    for label in ("Diluted EPS", "Basic EPS", "Diluted EPS Earnings"):
        if label in stmt.index:
            eps_row = stmt.loc[label]
            break

    if hist is None or hist.empty or "Close" not in hist.columns:
        return None

    close = hist["Close"].dropna().copy()
    if close.empty:
        return None
    if getattr(close.index, "tz", None) is not None:
        close.index = close.index.tz_localize(None)

    def _price_at(period) -> float | None:
        ts = pd.Timestamp(period)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        window = close[close.index <= ts]
        if not window.empty:
            return float(window.iloc[-1])
        later = close[close.index >= ts]
        if later.empty:
            return None
        return float(later.iloc[0])

    pes: list[float] = []

    if eps_row is not None:
        for period, eps in eps_row.items():
            eps_n = _finite(eps)
            if eps_n is None or eps_n <= 0:
                continue
            price = _price_at(period)
            if price is None:
                continue
            pe = price / eps_n
            if 1.0 < pe < 250:
                pes.append(pe)

    if len(pes) < MIN_PE_YEARS:
        ni_row = None
        for label in (
            "Net Income",
            "Net Income Common Stockholders",
            "Net Income From Continuing Operation Net Minority Interest",
        ):
            if label in stmt.index:
                ni_row = stmt.loc[label]
                break
        shares = None
        try:
            info = ticker.info or {}
            shares = _finite(info.get("sharesOutstanding"))
        except Exception:
            shares = None
        if ni_row is not None and shares and shares > 0:
            for period, ni in ni_row.items():
                ni_n = _finite(ni)
                if ni_n is None or ni_n <= 0:
                    continue
                price = _price_at(period)
                if price is None:
                    continue
                eps = ni_n / shares
                if eps <= 0:
                    continue
                pe = price / eps
                if 1.0 < pe < 250:
                    pes.append(pe)

    if len(pes) < MIN_PE_YEARS:
        return None
    return sum(pes) / len(pes)


def analyze_ticker(symbol: str) -> dict[str, Any]:
    """Analysiert eine Aktie. Wirft keine Exception nach außen."""
    result: dict[str, Any] = {
        "ticker": symbol,
        "name": symbol,
        "price": None,
        "sma200": None,
        "rsl": None,
        "trailing_pe": None,
        "avg_pe_5y": None,
        "gross_margin": None,
        "roe": None,
        "debt_to_equity": None,
        "above_sma200": False,
        "passed_koenig": False,
        "passed_trend": False,
        "reject_reason": None,
        "rank": None,
        "error": None,
    }

    try:
        ticker = yf.Ticker(symbol)
        try:
            info = ticker.info or {}
        except Exception as exc:
            log.warning("%s: info() fehlgeschlagen (%s)", symbol, exc)
            info = {}

        name = _pick_info(info, "longName", "shortName", "displayName")
        if name:
            result["name"] = str(name)

        result["gross_margin"] = _as_ratio(_pick_info(info, "grossMargins", "grossMargin"))
        result["roe"] = _as_ratio(_pick_info(info, "returnOnEquity"))
        result["debt_to_equity"] = _as_leverage(_pick_info(info, "debtToEquity"))
        result["trailing_pe"] = _finite(_pick_info(info, "trailingPE"))
        trailing_eps = _finite(_pick_info(info, "trailingEps", "epsTrailingTwelveMonths"))

        if any(result[k] is None for k in ("gross_margin", "roe", "debt_to_equity", "trailing_pe")):
            try:
                stmt_fund = fundamentals_from_statements(ticker)
            except Exception as exc:
                log.info("%s: Statement-Fallback fehlgeschlagen (%s)", symbol, exc)
                stmt_fund = {}
            result["gross_margin"] = result["gross_margin"] or stmt_fund.get("gross_margin")
            result["roe"] = result["roe"] or stmt_fund.get("roe")
            result["debt_to_equity"] = result["debt_to_equity"] or stmt_fund.get("debt_to_equity")
            if trailing_eps is None:
                trailing_eps = stmt_fund.get("trailing_eps")

        try:
            hist = ticker.history(period="6y", auto_adjust=True, timeout=30)
        except Exception as exc:
            result["error"] = f"Kursdaten nicht ladbar: {exc}"
            result["reject_reason"] = result["error"]
            return result

        if hist is None or hist.empty or "Close" not in hist.columns:
            result["error"] = "Keine Schlusskurse vorhanden"
            result["reject_reason"] = result["error"]
            return result

        close = hist["Close"].dropna()
        if close.empty:
            result["error"] = "Schlusskurs-Serie leer"
            result["reject_reason"] = result["error"]
            return result

        price = float(close.iloc[-1])
        result["price"] = price
        if result["trailing_pe"] is None and trailing_eps and trailing_eps > 0:
            result["trailing_pe"] = price / trailing_eps

        if len(close) < SMA_DAYS:
            result["reject_reason"] = f"Zu wenig Historie für GD{SMA_DAYS} ({len(close)} Tage)"
        else:
            sma = float(close.rolling(SMA_DAYS).mean().iloc[-1])
            result["sma200"] = sma
            result["above_sma200"] = price > sma

        if len(close) >= RSL_DAYS:
            avg_135 = float(close.tail(RSL_DAYS).mean())
            if avg_135 > 0:
                result["rsl"] = price / avg_135

        try:
            result["avg_pe_5y"] = _compute_avg_pe_5y(ticker, hist)
        except Exception as exc:
            log.info("%s: 5J-KGV nicht berechenbar (%s)", symbol, exc)
            result["avg_pe_5y"] = None

        reasons: list[str] = []
        if result["gross_margin"] is None:
            reasons.append("Bruttomarge fehlt")
        elif result["gross_margin"] <= MIN_GROSS_MARGIN:
            reasons.append(f"Bruttomarge {result['gross_margin']*100:.1f}% ≤ {MIN_GROSS_MARGIN*100:.0f}%")

        if result["roe"] is None:
            reasons.append("ROE fehlt")
        elif result["roe"] <= MIN_ROE:
            reasons.append(f"ROE {result['roe']*100:.1f}% ≤ {MIN_ROE*100:.0f}%")

        if result["debt_to_equity"] is None:
            reasons.append("Verschuldungsgrad fehlt")
        elif result["debt_to_equity"] >= MAX_DEBT_TO_EQUITY:
            reasons.append(f"D/E {result['debt_to_equity']:.2f} ≥ {MAX_DEBT_TO_EQUITY:.2f}")

        pe = result["trailing_pe"]
        avg_pe = result["avg_pe_5y"]
        if pe is None or pe <= 0:
            reasons.append("KGV fehlt oder ungültig")
        elif avg_pe is not None:
            if pe >= avg_pe:
                reasons.append(f"KGV {pe:.1f} nicht unter 5J-Schnitt {avg_pe:.1f}")
        elif pe >= MAX_PE_FALLBACK:
            reasons.append(f"KGV {pe:.1f} ≥ Fallback-Limit {MAX_PE_FALLBACK:.0f}")

        if reasons:
            result["passed_koenig"] = False
            result["reject_reason"] = "; ".join(reasons)
        else:
            result["passed_koenig"] = True

        if result["passed_koenig"]:
            if result["sma200"] is None:
                result["reject_reason"] = result.get("reject_reason") or f"GD{SMA_DAYS} nicht berechenbar"
            elif not result["above_sma200"]:
                result["reject_reason"] = f"Kurs unter GD{SMA_DAYS}"
            elif result["rsl"] is None:
                result["reject_reason"] = f"RSL-{RSL_DAYS} nicht berechenbar"
            else:
                result["passed_trend"] = True
                result["reject_reason"] = None

        return result

    except Exception as exc:
        log.exception("Unerwarteter Fehler bei %s", symbol)
        result["error"] = str(exc)
        result["reject_reason"] = f"Fehler: {exc}"
        return result


def scan_watchlist(symbols: list[str] | None = None) -> list[dict[str, Any]]:
    tickers = symbols or unique_watchlist()
    rows: list[dict[str, Any]] = []
    for i, symbol in enumerate(tickers):
        log.info("Analysiere %s (%s/%s)", symbol, i + 1, len(tickers))
        rows.append(analyze_ticker(symbol))
        if i < len(tickers) - 1 and REQUEST_PAUSE_SEC > 0:
            time.sleep(REQUEST_PAUSE_SEC)

    qualified = [r for r in rows if r.get("passed_trend") and r.get("rsl") is not None]
    qualified.sort(key=lambda r: r["rsl"], reverse=True)
    for idx, row in enumerate(qualified, start=1):
        row["rank"] = idx
    return rows


def qualified_ranked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = [r for r in rows if r.get("rank") is not None]
    ranked.sort(key=lambda r: r["rank"])
    return ranked
