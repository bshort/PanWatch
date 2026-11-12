"""Yahoo Finance headline RSS news vendor (US). Keyless; one feed per ticker.

Feed: https://feeds.finance.yahoo.com/rss/2.0/headline?s=<ticker>
Each <item> has title / link / description / pubDate (RFC 822) / guid.
Publish times are returned timezone-aware (UTC), like the other news vendors;
the since-window filter happens in MarketData.news().
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime

from marketdata.http import market_get, record_error
from marketdata.symbol import Market, Symbol
from marketdata.types import NewsArticle
from marketdata.vendors.base import NewsVendor

_FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
_HOST = "feeds.finance.yahoo.com"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
_TAG_RE = re.compile(r"<[^>]+>")
_DEFAULT_MAX_SYMBOLS = 30


def _text(node, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def parse_feed(xml_text: str, symbol: str) -> list[NewsArticle]:
    """Parse one Yahoo RSS document. Malformed XML or a non-RSS answer yields []."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[NewsArticle] = []
    for item in root.iter("item"):
        title = _text(item, "title")
        link = _text(item, "link")
        raw_date = _text(item, "pubDate")
        if not title or not raw_date:
            continue
        try:
            published = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        guid = _text(item, "guid") or link or title
        out.append(NewsArticle(
            source="yahoo",
            external_id=f"yahoo:{guid}",
            title=title,
            content=_TAG_RE.sub("", _text(item, "description")).strip(),
            publish_time=published.astimezone(timezone.utc),
            symbols=[symbol],
            importance=0,
            url=link,
        ))
    return out


class YahooNewsVendor(NewsVendor):
    name = "yahoo"
    supports_markets = {"US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NewsArticle]:
        cfg = config or {}
        max_symbols = int(cfg.get("max_symbols") or _DEFAULT_MAX_SYMBOLS)
        codes = [s.code.upper() for s in symbols if s.market == Market.US][:max_symbols]
        result: list[NewsArticle] = []
        for code in codes:
            text = market_get(
                _FEED_URL,
                host_key=_HOST,
                params={"s": code, "region": "US", "lang": "en-US"},
                headers={"User-Agent": _UA},
                min_interval_s=0.3,
                timeout=10,
                retries=1,
                parse="text",
                proxy=cfg.get("proxy") or None,
                symbol=code,
                log_label="Yahoo news",
            )
            if text is None:
                continue
            articles = parse_feed(text, code)
            if not articles and "<rss" not in text[:500].lower():
                record_error(f"Yahoo news {code}: response was not an RSS feed")
            result.extend(articles)
        return result
