"""SEC EDGAR filings as events (US). Free and official; no key.

- Ticker → CIK: https://www.sec.gov/files/company_tickers.json (cached for a day).
- Filings:      https://data.sec.gov/submissions/CIK##########.json → filings.recent.

SEC asks automated clients to send a descriptive User-Agent, ideally with a
contact address ("Name email@example.com"), and to stay under 10 requests a
second. Set it per data source as config.user_agent; the default is generic.
Publish times are the filing's EDGAR acceptance time, timezone-aware (UTC).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from marketdata.http import market_get, record_error
from marketdata.symbol import Market, Symbol
from marketdata.types import EventItem
from marketdata.vendors.base import EventsVendor

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
# www.sec.gov answers 403 to some free-form agents (spaces, parentheses); a product token passes.
_DEFAULT_UA = "PanWatch/1.0"
_TICKER_TTL_S = 24 * 3600

_ticker_lock = threading.Lock()
_ticker_map: dict[str, int] = {}
_ticker_loaded_at = 0.0

# 8-K item → (description, event_type, importance)
_8K_ITEMS: dict[str, tuple[str, str, int]] = {
    "1.01": ("Material agreement", "major", 2),
    "1.02": ("Termination of a material agreement", "major", 2),
    "1.03": ("Bankruptcy or receivership", "major", 3),
    "2.01": ("Acquisition or disposition of assets", "restructuring", 3),
    "2.02": ("Results of operations", "earnings", 3),
    "2.03": ("New financial obligation", "financing", 2),
    "2.05": ("Exit or restructuring costs", "restructuring", 3),
    "2.06": ("Material impairment", "major", 3),
    "3.01": ("Delisting or listing-rule notice", "regulatory", 3),
    "3.02": ("Unregistered sale of equity", "financing", 2),
    "3.03": ("Change to shareholder rights", "major", 2),
    "4.01": ("Change of auditor", "regulatory", 3),
    "4.02": ("Prior financial statements no longer reliable", "regulatory", 3),
    "5.01": ("Change in control", "restructuring", 3),
    "5.02": ("Director or officer change", "major", 2),
    "5.03": ("Charter or bylaw amendment", "notice", 1),
    "5.07": ("Shareholder vote results", "notice", 1),
    "7.01": ("Regulation FD disclosure", "notice", 1),
    "8.01": ("Other events", "notice", 1),
}

# form → (description, event_type, importance); "/A" amendments share their base form.
_FORMS: dict[str, tuple[str, str, int]] = {
    "10-K": ("Annual report", "earnings", 3),
    "10-Q": ("Quarterly report", "earnings", 3),
    "20-F": ("Annual report (foreign issuer)", "earnings", 3),
    "40-F": ("Annual report (Canadian issuer)", "earnings", 3),
    "6-K": ("Current report (foreign issuer)", "notice", 1),
    "3": ("Initial insider ownership", "insider", 1),
    "4": ("Insider transaction", "insider", 1),
    "5": ("Annual insider ownership", "insider", 1),
    "144": ("Proposed insider sale", "insider", 1),
    "SC 13D": ("Activist stake over 5%", "insider", 2),
    "SC 13G": ("Passive stake over 5%", "insider", 1),
    "SCHEDULE 13D": ("Activist stake over 5%", "insider", 2),
    "SCHEDULE 13G": ("Passive stake over 5%", "insider", 1),
    "S-1": ("Registration statement", "financing", 2),
    "S-3": ("Shelf registration", "financing", 2),
    "S-8": ("Employee stock plan registration", "financing", 0),
    "F-1": ("Registration statement (foreign issuer)", "financing", 2),
    "F-3": ("Shelf registration (foreign issuer)", "financing", 2),
    "424B2": ("Prospectus", "financing", 1),
    "424B3": ("Prospectus", "financing", 1),
    "424B4": ("Prospectus", "financing", 1),
    "424B5": ("Prospectus supplement", "financing", 1),
    "DEF 14A": ("Proxy statement", "notice", 1),
    "SC TO-T": ("Tender offer", "restructuring", 3),
    "SC 14D9": ("Tender offer response", "restructuring", 3),
    "425": ("Merger communication", "restructuring", 2),
    "8-K": ("Current report", "notice", 1),
    # Exchange notice that a class of securities (often a bond series) is being delisted.
    "25-NSE": ("Exchange delisting notice for a security class", "regulatory", 1),
    "25": ("Delisting notice for a security class", "regulatory", 1),
}

_IMPORTANCE_ORDER = {"earnings": 5, "restructuring": 4, "regulatory": 3, "major": 2, "financing": 1}


def _headers(config: dict) -> dict:
    return {"User-Agent": str((config or {}).get("user_agent") or _DEFAULT_UA)}


def _normalize_ticker(code: str) -> str:
    # PanWatch writes share classes as BRK.B; SEC lists them as BRK-B.
    return code.strip().upper().replace(".", "-")


def load_ticker_map(config: dict) -> dict[str, int]:
    global _ticker_map, _ticker_loaded_at
    with _ticker_lock:
        if _ticker_map and time.time() - _ticker_loaded_at < _TICKER_TTL_S:
            return _ticker_map
    data = market_get(
        _TICKERS_URL, host_key="www.sec.gov", headers=_headers(config),
        min_interval_s=0.15, timeout=20, retries=1, parse="json", log_label="SEC tickers",
    )
    if not isinstance(data, dict):
        return _ticker_map  # keep a stale map rather than none
    mapping: dict[str, int] = {}
    for row in data.values():
        if isinstance(row, dict) and row.get("ticker") and row.get("cik_str") is not None:
            try:
                mapping[str(row["ticker"]).upper()] = int(row["cik_str"])
            except (TypeError, ValueError):
                continue
    with _ticker_lock:
        if mapping:
            _ticker_map = mapping
            _ticker_loaded_at = time.time()
        return _ticker_map


def classify(form: str, items: str) -> tuple[str, str, int]:
    """(title detail, event_type, importance) for a filing."""
    base = form[:-2] if form.endswith("/A") else form
    if base == "8-K":
        found = [_8K_ITEMS[i.strip()] for i in (items or "").split(",") if i.strip() in _8K_ITEMS]
        if found:
            found.sort(key=lambda x: (x[2], _IMPORTANCE_ORDER.get(x[1], 0)), reverse=True)
            detail = "; ".join(d for d, _, _ in found)
            return detail, found[0][1], found[0][2]
    if base in _FORMS:
        return _FORMS[base]
    return "", "notice", 0


def _publish_time(accepted: str, filing_date: str) -> datetime | None:
    if accepted:
        try:
            return datetime.fromisoformat(accepted.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    if filing_date:
        try:
            return datetime.strptime(filing_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def parse_submissions(data: dict, symbol: str, cik: int, *, since: datetime,
                      forms: set[str] | None = None, limit: int = 50) -> list[EventItem]:
    recent = ((data or {}).get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    out: list[EventItem] = []

    def col(name: str, i: int) -> str:
        values = recent.get(name) or []
        return str(values[i]) if i < len(values) and values[i] is not None else ""

    for i, acc in enumerate(accessions):
        form = col("form", i)
        if not acc or not form:
            continue
        if forms and form not in forms and form.removesuffix("/A") not in forms:
            continue
        published = _publish_time(col("acceptanceDateTime", i), col("filingDate", i))
        if published is None:
            continue  # never substitute fetch time for a missing filing time
        if published < since:
            continue
        detail, event_type, importance = classify(form, col("items", i))
        doc = col("primaryDocument", i)
        acc_path = acc.replace("-", "")
        url = (_ARCHIVE_URL.format(cik=cik, acc=acc_path, doc=doc) if doc
               else _ARCHIVE_URL.format(cik=cik, acc=acc_path, doc=f"{acc}-index.htm"))
        description = detail or col("primaryDocDescription", i)
        out.append(EventItem(
            source="sec",
            external_id=acc,
            event_type=event_type,
            title=f"{form}: {description}" if description and description != form else form,
            publish_time=published,
            symbols=[symbol],
            importance=importance,
            url=url,
        ))
        if len(out) >= limit:
            break
    return out


class SecFilingsVendor(EventsVendor):
    name = "sec"
    supports_markets = {"US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[EventItem]:
        cfg = config or {}
        codes = [s.code for s in symbols if s.market == Market.US]
        if not codes:
            return []
        tickers = load_ticker_map(cfg)
        if not tickers:
            record_error("SEC: could not load the ticker list")
            return []

        since_days = int(cfg.get("since_days") or 7)
        since = datetime.now(timezone.utc) - timedelta(days=max(since_days, 1))
        forms = {str(f).strip().upper() for f in (cfg.get("forms") or []) if str(f).strip()} or None
        limit = int(cfg.get("page_size") or 50)

        out: list[EventItem] = []
        for code in codes:
            cik = tickers.get(_normalize_ticker(code))
            if cik is None:
                record_error(f"SEC: no CIK for ticker {code} (ETFs and funds have none)")
                continue
            data = market_get(
                _SUBMISSIONS_URL.format(cik=cik), host_key="data.sec.gov", headers=_headers(cfg),
                min_interval_s=0.15, timeout=15, retries=1, parse="json",
                symbol=code, log_label="SEC filings",
            )
            if isinstance(data, dict):
                out.extend(parse_submissions(data, code, cik, since=since, forms=forms, limit=limit))
        return out
