"""事件取数走 marketdata 包路由测试"""
import asyncio
from datetime import datetime, timedelta

import src.platform.marketdata.collectors.events_collector as ec


def test_fetch_events_uses_marketdata(monkeypatch):
    """走 marketdata 包的 events,转换为 PanWatch EventItem"""
    from marketdata.types import EventItem as MdEventItem

    now = datetime.now()

    class _MD:
        def events(self, symbols, *, market="CN", since_days=7):
            return [
                MdEventItem(
                    source="eastmoney",
                    external_id="AN001",
                    event_type="earnings",
                    title="业绩预告",
                    publish_time=now,
                    symbols=["600519"],
                    importance=3,
                    url="https://data.eastmoney.com/notices/detail/600519/AN001.html",
                )
            ]

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())

    collector = ec.EastMoneyEventsCollector()
    out = asyncio.run(collector.fetch_events(["600519"]))

    assert len(out) == 1
    item = out[0]
    assert isinstance(item, ec.EventItem)
    assert item.source == "eastmoney"
    assert item.external_id == "AN001"
    assert item.event_type == "earnings"
    assert item.title == "业绩预告"
    assert item.symbols == ["600519"]
    assert item.importance == 3


def test_fetch_events_no_symbols_returns_empty(monkeypatch):
    """symbols 为空时短路返回空列表,不触达 marketdata。"""

    def _boom():
        raise AssertionError("不应调用 get_market_data")

    monkeypatch.setattr(ec, "get_market_data", _boom)

    collector = ec.EastMoneyEventsCollector()
    out = asyncio.run(collector.fetch_events([]))
    assert out == []


def test_fetch_events_applies_since_filter(monkeypatch):
    """md.events 按天窗返回的结果仍需用原 since 精确重过滤。"""
    from marketdata.types import EventItem as MdEventItem

    now = datetime.now()
    old_time = now - timedelta(days=10)
    since = now - timedelta(days=3)

    class _MD:
        def events(self, symbols, *, market="CN", since_days=7):
            return [
                MdEventItem(
                    source="eastmoney", external_id="NEW", event_type="notice",
                    title="新公告", publish_time=now, symbols=["600519"],
                    importance=0, url="",
                ),
                MdEventItem(
                    source="eastmoney", external_id="OLD", event_type="notice",
                    title="旧公告", publish_time=old_time, symbols=["600519"],
                    importance=0, url="",
                ),
            ]

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())

    collector = ec.EastMoneyEventsCollector()
    out = asyncio.run(collector.fetch_events(["600519"], since=since))

    assert len(out) == 1
    assert out[0].external_id == "NEW"


def test_fetch_events_mixed_markets_and_timezones(monkeypatch):
    """A watchlist mixing A-shares and US tickers asks marketdata to group by market
    (market=None), and naive CN times sort and filter next to aware US times."""
    from datetime import timezone

    from marketdata.types import EventItem as MdEventItem

    seen = {}
    now_utc = datetime.now(timezone.utc)

    class _MD:
        def events(self, symbols, *, market="CN", since_days=7):
            seen["market"], seen["symbols"] = market, symbols
            return [
                MdEventItem(source="eastmoney", external_id="CN1", event_type="notice", title="公告",
                            publish_time=datetime.now() - timedelta(days=1), symbols=["600519"],
                            importance=0, url=""),
                MdEventItem(source="sec", external_id="S1", event_type="earnings", title="8-K",
                            publish_time=now_utc - timedelta(hours=1), symbols=["AAPL"],
                            importance=3, url=""),
                MdEventItem(source="sec", external_id="S_OLD", event_type="notice", title="old",
                            publish_time=now_utc - timedelta(days=20), symbols=["AAPL"],
                            importance=0, url=""),
            ]

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())

    since = datetime.now() - timedelta(days=7)
    out = asyncio.run(ec.EastMoneyEventsCollector().fetch_events(["600519", "AAPL"], since=since))

    assert seen == {"market": None, "symbols": ["600519", "AAPL"]}
    assert [e.external_id for e in out] == ["S1", "CN1"]
