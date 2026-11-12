"""Finnhub company news vendor (US). Needs a free API key from finnhub.io.

GET https://finnhub.io/api/v1/company-news?symbol=AAPL&from=YYYY-MM-DD&to=YYYY-MM-DD
The key goes in the X-Finnhub-Token header, never the query string, so it
cannot end up in logged URLs or error messages. The free tier allows about
60 calls a minute; requests are spaced 1.1 s apart per host.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from marketdata.errors import VendorError
from marketdata.http import market_get
from marketdata.symbol import Market, Symbol
from marketdata.types import NewsArticle
from marketdata.vendors.base import NewsVendor

_URL = "https://finnhub.io/api/v1/company-news"
_HOST = "finnhub.io"
_DEFAULT_MAX_SYMBOLS = 20


def api_key(config: dict) -> str:
    """The key from the data source config, else the FINNHUB_API_KEY environment variable."""
    return str((config or {}).get("api_key") or os.environ.get("FINNHUB_API_KEY") or "").strip()


def parse_items(items, symbol: str) -> list[NewsArticle]:
    if not isinstance(items, list):
        return []
    out: list[NewsArticle] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        headline = str(it.get("headline") or "").strip()
        ts = it.get("datetime")
        if not headline or not isinstance(ts, (int, float)) or ts <= 0:
            continue
        publisher = str(it.get("source") or "").strip()
        summary = str(it.get("summary") or "").strip()
        out.append(NewsArticle(
            source="finnhub",
            external_id=f"finnhub:{it.get('id') or it.get('url') or headline}",
            title=headline,
            content=f"{summary} ({publisher})" if summary and publisher else summary,
            publish_time=datetime.fromtimestamp(ts, tz=timezone.utc),
            symbols=[symbol],
            importance=0,
            url=str(it.get("url") or ""),
        ))
    return out


class FinnhubNewsVendor(NewsVendor):
    name = "finnhub"
    supports_markets = {"US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NewsArticle]:
        cfg = config or {}
        codes = [s.code.upper() for s in symbols if s.market == Market.US]
        if not codes:
            return []
        key = api_key(cfg)
        if not key:
            raise VendorError("Finnhub needs an API key: add it to this data source (free at finnhub.io)")

        max_symbols = int(cfg.get("max_symbols") or _DEFAULT_MAX_SYMBOLS)
        since_hours = int(cfg.get("since_hours") or 24)
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=max(1, (since_hours + 23) // 24))

        result: list[NewsArticle] = []
        for code in codes[:max_symbols]:
            items = market_get(
                _URL,
                host_key=_HOST,
                params={"symbol": code, "from": start.isoformat(), "to": today.isoformat()},
                headers={"X-Finnhub-Token": key},
                min_interval_s=1.1,
                timeout=10,
                retries=1,
                parse="json",
                proxy=cfg.get("proxy") or None,
                symbol=code,
                log_label="Finnhub news",
            )
            result.extend(parse_items(items, code))
        return result
