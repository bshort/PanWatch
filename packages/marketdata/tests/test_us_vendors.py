"""US data sources: Yahoo RSS news, Finnhub news, SEC filings, yfinance
fundamentals / dividends / calendar, plus the market routing in
MarketData.news() and the aggregation in MarketData.events().

All offline: market_get and yfinance are replaced with fakes.
"""
from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

import marketdata.vendors.finnhub as fh
import marketdata.vendors.sec as sec
import marketdata.vendors.yahoo as yahoo
import marketdata.vendors.yfinance as yv
from marketdata.client import MarketData, event_sort_time
from marketdata.defaults import StaticConfigProvider
from marketdata.errors import VendorError
from marketdata.http import capture_errors
from marketdata.ports import SourceConfig
from marketdata.symbol import Symbol
from marketdata.types import EventItem, NewsArticle
from marketdata.vendors.base import EventsVendor, NewsVendor

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Yahoo RSS news
# ---------------------------------------------------------------------------

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Yahoo! Finance: AAPL News</title>
<item>
  <title>Apple beats estimates</title>
  <link>https://finance.yahoo.com/news/apple-beats.html</link>
  <description>Apple &lt;b&gt;reported&lt;/b&gt; record revenue.</description>
  <guid isPermaLink="false">abc-123</guid>
  <pubDate>Wed, 11 Nov 2026 21:05:00 +0000</pubDate>
</item>
<item>
  <title>No date, skipped</title>
  <link>https://finance.yahoo.com/news/x.html</link>
</item>
</channel></rss>"""


def test_yahoo_parse_feed():
    out = yahoo.parse_feed(_RSS, "AAPL")
    assert len(out) == 1
    a = out[0]
    assert a.source == "yahoo" and a.external_id == "yahoo:abc-123"
    assert a.title == "Apple beats estimates"
    assert a.content == "Apple reported record revenue."
    assert a.publish_time == datetime(2026, 11, 11, 21, 5, tzinfo=UTC)
    assert a.symbols == ["AAPL"] and a.url.endswith("apple-beats.html")


def test_yahoo_parse_feed_rejects_garbage():
    assert yahoo.parse_feed("<html><body>blocked", "AAPL") == []


def test_yahoo_vendor_requests_us_symbols_only(monkeypatch):
    calls = []

    def fake_get(url, *, params=None, **kw):
        calls.append(params["s"])
        return _RSS

    monkeypatch.setattr(yahoo, "market_get", fake_get)
    out = yahoo.YahooNewsVendor().fetch([Symbol.parse("aapl"), Symbol.parse("600519")], {})
    assert calls == ["AAPL"]
    assert [a.symbols for a in out] == [["AAPL"]]


def test_yahoo_vendor_records_non_rss_answer(monkeypatch):
    monkeypatch.setattr(yahoo, "market_get", lambda *a, **k: "<html>Will be right back</html>")
    with capture_errors() as errs:
        assert yahoo.YahooNewsVendor().fetch([Symbol.parse("AAPL")], {}) == []
    assert any("not an RSS feed" in e for e in errs)


# ---------------------------------------------------------------------------
# Finnhub news
# ---------------------------------------------------------------------------

def test_finnhub_without_key_raises(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    with pytest.raises(VendorError, match="API key"):
        fh.FinnhubNewsVendor().fetch([Symbol.parse("AAPL")], {})


def test_finnhub_sends_key_in_header_not_query(monkeypatch):
    seen = {}

    def fake_get(url, *, params=None, headers=None, **kw):
        seen["params"], seen["headers"] = params, headers
        return [
            {"id": 7, "headline": "Apple news", "summary": "Sum", "source": "Reuters",
             "datetime": 1_794_000_000, "url": "https://example.com/n"},
            {"id": 8, "headline": "", "datetime": 1_794_000_000},
        ]

    monkeypatch.setattr(fh, "market_get", fake_get)
    out = fh.FinnhubNewsVendor().fetch([Symbol.parse("AAPL")], {"api_key": "k123", "since_hours": 48})
    assert seen["headers"] == {"X-Finnhub-Token": "k123"}
    assert "token" not in seen["params"] and "k123" not in str(seen["params"])
    assert seen["params"]["symbol"] == "AAPL"
    from_day = date.fromisoformat(seen["params"]["from"])
    to_day = date.fromisoformat(seen["params"]["to"])
    assert (to_day - from_day).days == 2
    assert len(out) == 1
    assert out[0].external_id == "finnhub:7" and out[0].content == "Sum (Reuters)"
    assert out[0].publish_time.tzinfo is not None


def test_finnhub_key_from_environment(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "envkey")
    assert fh.api_key({}) == "envkey"
    assert fh.api_key({"api_key": " cfg "}) == "cfg"


# ---------------------------------------------------------------------------
# SEC EDGAR filings
# ---------------------------------------------------------------------------

def _submissions(now: datetime) -> dict:
    recent_ts = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    old_ts = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "filings": {"recent": {
            "accessionNumber": ["0000320193-26-000101", "0000320193-26-000100", "0000320193-26-000050"],
            "form": ["8-K", "4", "10-Q"],
            "items": ["2.02,9.01", "", ""],
            "acceptanceDateTime": [recent_ts, recent_ts, old_ts],
            "filingDate": ["", "", ""],
            "primaryDocument": ["aapl-8k.htm", "", "aapl-10q.htm"],
            "primaryDocDescription": ["8-K", "FORM 4", "10-Q"],
        }}
    }


def test_sec_classify():
    assert sec.classify("8-K", "2.02,9.01") == ("Results of operations", "earnings", 3)
    detail, kind, imp = sec.classify("8-K", "5.02,1.01")
    assert kind == "major" and imp == 2 and "officer" in detail and "agreement" in detail
    assert sec.classify("10-K/A", "")[1:] == ("earnings", 3)
    assert sec.classify("WEIRD", "") == ("", "notice", 0)


def test_sec_parse_submissions_window_forms_and_urls():
    now = datetime.now(UTC)
    since = now - timedelta(days=7)
    out = sec.parse_submissions(_submissions(now), "AAPL", 320193, since=since)
    assert [e.external_id for e in out] == ["0000320193-26-000101", "0000320193-26-000100"]
    k8 = out[0]
    assert k8.source == "sec" and k8.event_type == "earnings" and k8.importance == 3
    assert k8.title == "8-K: Results of operations"
    assert k8.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/aapl-8k.htm"
    assert k8.publish_time.tzinfo is not None
    # no primary document → the filing index
    assert out[1].url.endswith("/000032019326000100/0000320193-26-000100-index.htm")

    only_8k = sec.parse_submissions(_submissions(now), "AAPL", 320193, since=since, forms={"8-K"})
    assert [e.external_id for e in only_8k] == ["0000320193-26-000101"]


def test_sec_vendor_maps_tickers_and_sends_user_agent(monkeypatch):
    now = datetime.now(UTC)
    seen = []

    def fake_get(url, *, headers=None, **kw):
        seen.append((url, headers))
        if url == sec._TICKERS_URL:
            return {"0": {"cik_str": 320193, "ticker": "AAPL"}, "1": {"cik_str": 1067983, "ticker": "BRK-B"}}
        return _submissions(now)

    monkeypatch.setattr(sec, "market_get", fake_get)
    monkeypatch.setattr(sec, "_ticker_map", {})
    monkeypatch.setattr(sec, "_ticker_loaded_at", 0.0)

    cfg = {"user_agent": "Jane Doe jane@example.com", "since_days": 7}
    out = sec.SecFilingsVendor().fetch([Symbol.parse("AAPL"), Symbol.parse("BRK.B")], cfg)
    urls = [u for u, _ in seen]
    assert urls[0] == sec._TICKERS_URL
    assert "CIK0000320193.json" in urls[1] and "CIK0001067983.json" in urls[2]
    assert all(h == {"User-Agent": "Jane Doe jane@example.com"} for _, h in seen)
    assert {e.symbols[0] for e in out} == {"AAPL", "BRK.B"}

    # the ticker list is cached
    seen.clear()
    sec.SecFilingsVendor().fetch([Symbol.parse("AAPL")], cfg)
    assert [u for u, _ in seen if u == sec._TICKERS_URL] == []


def test_sec_vendor_unknown_ticker_records_error(monkeypatch):
    monkeypatch.setattr(sec, "_ticker_map", {"AAPL": 320193})
    monkeypatch.setattr(sec, "_ticker_loaded_at", 1e18)
    with capture_errors() as errs:
        assert sec.SecFilingsVendor().fetch([Symbol.parse("SPY")], {}) == []
    assert any("no CIK" in e for e in errs)


# ---------------------------------------------------------------------------
# yfinance fundamentals / dividends / calendar
# ---------------------------------------------------------------------------

def _fake_yf(monkeypatch, **attrs):
    fake = types.ModuleType("yfinance")

    class Ticker:
        def __init__(self, ticker):
            self.ticker = ticker
            for k, v in attrs.items():
                setattr(self, k, v)

    fake.Ticker = Ticker
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_info_to_fundamentals_units():
    info = {
        "longName": "Apple Inc.", "trailingPE": 38.8, "priceToBook": 46.1,
        "marketCap": 5_168_079_831_040, "dividendYield": 0.4, "returnOnEquity": 1.42,
        "grossMargins": 0.47, "profitMargins": 0.26, "revenueGrowth": 0.08,
        "trailingEps": 7.5, "totalRevenue": 4.2e11, "mostRecentQuarter": 1_790_726_400,
        "earningsGrowth": float("nan"),
    }
    f = yv.info_to_fundamentals(Symbol.parse("AAPL"), info)
    assert f.name == "Apple Inc." and f.market == "US"
    assert f.total_market_value == pytest.approx(51680.798, rel=1e-6)  # units of 100 million
    assert f.roe == pytest.approx(142.0) and f.gross_margin == pytest.approx(47.0)
    assert f.dividend_yield == 0.4
    assert f.net_profit_yoy is None  # NaN is dropped
    assert f.report_date == "2026-09-30"


def test_info_to_fundamentals_empty_is_none():
    assert yv.info_to_fundamentals(Symbol.parse("AAPL"), {"longName": "X"}) is None


def test_yfinance_fundamentals_vendor(monkeypatch):
    _fake_yf(monkeypatch, info={"trailingPE": 20.0, "marketCap": 1e9})
    out = yv.YFinanceFundamentalsVendor().fetch([Symbol.parse("NVDA")], {})
    assert len(out) == 1 and out[0].pe_ttm == 20.0 and out[0].total_market_value == 10.0


def test_yfinance_dividend_vendor_newest_first(monkeypatch):
    pd = pytest.importorskip("pandas")
    series = pd.Series(
        [0.25, 0.26, 0.27],
        index=pd.to_datetime(["2026-02-09", "2026-05-11", "2026-08-10"]).tz_localize("America/New_York"),
    )
    _fake_yf(monkeypatch, dividends=series)
    out = yv.YFinanceDividendVendor().fetch([Symbol.parse("AAPL")], {"limit": 2})
    assert [(d.ex_date, d.dividend_per_share) for d in out] == [("2026-08-10", 0.27), ("2026-05-11", 0.26)]


def test_calendar_to_events():
    now = datetime.now(UTC)
    cal = {
        "Earnings Date": [date(2027, 1, 28), date(2027, 2, 1)],
        "Earnings Average": 2.914,
        "Ex-Dividend Date": date(2020, 1, 1),  # older than the window → dropped
        "Dividend Date": date(2020, 1, 15),
    }
    out = yv.calendar_to_events(Symbol.parse("AAPL"), cal, since=now - timedelta(days=7))
    assert len(out) == 1
    e = out[0]
    assert e.source == "yfinance" and e.event_type == "earnings" and e.importance == 3
    assert e.title == "Earnings report expected 2027-01-28–2027-02-01 (EPS estimate 2.91)"
    assert e.external_id == "AAPL:earnings:2027-01-28"
    assert e.publish_time.tzinfo is not None and e.publish_time.date() == date(2027, 1, 28)


def test_calendar_ex_dividend_in_window():
    now = datetime.now(UTC)
    ex = (now + timedelta(days=3)).date()
    out = yv.calendar_to_events(Symbol.parse("AAPL"), {"Ex-Dividend Date": ex}, since=now - timedelta(days=7))
    assert [(e.event_type, e.importance) for e in out] == [("dividend", 2)]
    assert out[0].title == f"Ex-dividend date {ex.isoformat()}"


# ---------------------------------------------------------------------------
# MarketData routing / aggregation
# ---------------------------------------------------------------------------

class _RecordingNews(NewsVendor):
    def __init__(self, name, markets):
        self.name, self.supports_markets, self.calls = name, markets, []

    def fetch(self, symbols, config):
        self.calls.append([(s.market.value, s.code) for s in symbols])
        return [NewsArticle(source=self.name, external_id=f"{self.name}:{s.code}", title=s.code,
                            content="", publish_time=datetime(2026, 11, 11, tzinfo=UTC), symbols=[s.code])
                for s in symbols]


def test_news_routes_us_symbols_to_us_vendor_under_cn_request():
    md = MarketData(config=StaticConfigProvider({"news": [
        SourceConfig(vendor="cn", priority=0), SourceConfig(vendor="us", priority=1),
    ]}))
    cn, us = _RecordingNews("cn", {"CN"}), _RecordingNews("us", {"US"})
    md._news_vendors = {"cn": cn, "us": us}

    out = md.news(["600519", "AAPL", "00700"])  # market defaults to CN, as the host calls it

    # the CN vendor keeps receiving everything (unchanged behaviour)
    assert cn.calls == [[("CN", "600519"), ("CN", "AAPL"), ("CN", "00700")]]
    # the US vendor receives only the US ticker, typed as US
    assert us.calls == [[("US", "AAPL")]]
    assert {a.external_id for a in out} == {"cn:600519", "cn:AAPL", "cn:00700", "us:AAPL"}


def test_news_skips_vendor_with_no_matching_symbols():
    md = MarketData(config=StaticConfigProvider({"news": [SourceConfig(vendor="us")]}))
    us = _RecordingNews("us", {"US"})
    md._news_vendors = {"us": us}
    assert md.news(["600519"]) == []
    assert us.calls == []


class _FixedEvents(EventsVendor):
    def __init__(self, name, markets, items, boom=False):
        self.name, self.supports_markets, self.items, self.boom, self.calls = name, markets, items, boom, []

    def fetch(self, symbols, config):
        self.calls.append(([s.code for s in symbols], config.get("since_days")))
        if self.boom:
            raise RuntimeError("down")
        return list(self.items)


def _ev(source, ext, when, importance=0):
    return EventItem(source=source, external_id=ext, event_type="notice", title=ext,
                     publish_time=when, symbols=[], importance=importance, url="")


def test_events_aggregates_sources_and_groups_by_market():
    cn_time = datetime(2026, 11, 11, 9, 0)  # naive, Asia/Shanghai → 01:00 UTC
    cn = _FixedEvents("eastmoney", {"CN"}, [_ev("eastmoney", "AN1", cn_time)])
    sec_v = _FixedEvents("sec", {"US"}, [_ev("sec", "S1", datetime(2026, 11, 11, 2, 0, tzinfo=UTC))])
    cal = _FixedEvents("yfinance", {"US"}, [
        _ev("yfinance", "Y1", datetime(2027, 1, 28, tzinfo=UTC), 3),
        _ev("sec", "S1", datetime(2026, 11, 11, 2, 0, tzinfo=UTC)),  # duplicate key → dropped
    ])
    broken = _FixedEvents("broken", {"US"}, [], boom=True)
    md = MarketData(config=StaticConfigProvider({"events": [
        SourceConfig(vendor="eastmoney", priority=0), SourceConfig(vendor="sec", priority=1),
        SourceConfig(vendor="yfinance", priority=2), SourceConfig(vendor="broken", priority=3),
    ]}))
    md._events_vendors = {"eastmoney": cn, "sec": sec_v, "yfinance": cal, "broken": broken}

    with capture_errors() as errs:
        out = md.events(["600519", "AAPL"], market=None, since_days=30)

    assert cn.calls == [(["600519"], 30)]
    assert sec_v.calls == [(["AAPL"], 30)] and cal.calls == [(["AAPL"], 30)]
    # newest first across naive CN and aware US times
    assert [e.external_id for e in out] == ["Y1", "S1", "AN1"]
    assert any("broken" in e for e in errs)


def test_events_explicit_market_keeps_old_contract():
    cn = _FixedEvents("eastmoney", {"CN"}, [_ev("eastmoney", "AN1", datetime(2026, 11, 11))])
    sec_v = _FixedEvents("sec", {"US"}, [])
    md = MarketData(config=StaticConfigProvider({"events": [
        SourceConfig(vendor="eastmoney"), SourceConfig(vendor="sec"),
    ]}))
    md._events_vendors = {"eastmoney": cn, "sec": sec_v}
    out = md.events(["600519"], market="CN")
    assert [e.external_id for e in out] == ["AN1"] and sec_v.calls == []


def test_event_sort_time_treats_naive_as_shanghai():
    assert event_sort_time(datetime(2026, 1, 1, 8, 0)) == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def test_sec_classify_delisting_notice():
    assert sec.classify("25-NSE", "") == ("Exchange delisting notice for a security class", "regulatory", 1)
