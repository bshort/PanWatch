"""YFinance vendors(可选,HK/US):行情 / 基本面 / 分红 / 财报与除息日历。无状态 + 惰性 import;缺库抛 VendorError。"""

from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from marketdata.errors import VendorError
from marketdata.http import record_error
from marketdata.symbol import Market, Symbol
from marketdata.types import DividendItem, EventItem, Fundamentals, Quote
from marketdata.vendors.base import DividendVendor, FundamentalsVendor, QuoteVendor
from marketdata.vendors.base import EventsVendor as EventsVendorBase

logger = logging.getLogger(__name__)


def _yf_ticker(sym: Symbol) -> str:
    if sym.market == Market.HK:
        return f"{int(sym.code):04d}.HK" if sym.code.isdigit() else f"{sym.code}.HK"
    return sym.code


class YFinanceQuoteVendor(QuoteVendor):
    name = "yfinance"
    supports_markets = {"HK", "US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []
        try:
            import yfinance as yf
        except ImportError as e:
            raise VendorError("yfinance 未安装,执行 `pip install yfinance` 后启用") from e

        out: list[Quote] = []
        for s in symbols:
            try:
                info = yf.Ticker(_yf_ticker(s)).fast_info
                # fast_info.get() uses camelCase mapping keys; [] accepts snake_case.
                last_value = info.get("lastPrice") or info.get("last_price")
                if last_value is None:
                    record_error(f"yfinance {_yf_ticker(s)}: 返回空(last_price 缺失,可能 Yahoo 不可达/被限流/需要代理)")
                    continue
                last = float(last_value)
                prev_value = info.get("previousClose") or info.get("previous_close")
                prev = float(prev_value) if prev_value is not None else None
                chg = last - prev if prev else 0.0
                pct = (chg / prev * 100) if prev else 0.0
                out.append(Quote(
                    symbol=s.code, market=s.market.value, name="",
                    current_price=last, prev_close=prev,
                    open_price=float(info.get("open") or 0),
                    high_price=float(info.get("dayHigh") or info.get("day_high") or 0),
                    low_price=float(info.get("dayLow") or info.get("day_low") or 0),
                    change_amount=chg, change_pct=pct,
                    volume=float(
                        info.get("lastVolume") or info.get("last_volume") or 0
                    ),
                ))
            except Exception as e:
                logger.debug(f"yfinance 拉取 {s.code} 失败: {e}")
                record_error(f"yfinance {_yf_ticker(s)}: {type(e).__name__}: {e}")
        return out


# ---------------------------------------------------------------------------
# Fundamentals / dividends / calendar (US/HK) — all through yfinance, which
# handles Yahoo's crumb and cookie for the quoteSummary endpoints. Values that
# Yahoo leaves out stay None.
# ---------------------------------------------------------------------------

def _import_yf():
    try:
        import yfinance as yf
    except ImportError as e:
        raise VendorError("yfinance 未安装,执行 `pip install yfinance` 后启用") from e
    return yf


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # drop NaN


def _pct(value) -> float | None:
    """Yahoo ratios such as returnOnEquity are fractions (0.25); Fundamentals uses percent."""
    f = _num(value)
    return None if f is None else f * 100


def _per_100m(value) -> float | None:
    """Fundamentals stores market value in units of 100 million (亿), like the other vendors."""
    f = _num(value)
    return None if f is None else f / 1e8


def _epoch_to_date(value) -> str:
    f = _num(value)
    if f is None:
        return ""
    try:
        return datetime.fromtimestamp(f, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def info_to_fundamentals(sym: Symbol, info: dict) -> Fundamentals | None:
    """Map a yfinance Ticker.info dict to Fundamentals; None when Yahoo returned nothing usable."""
    if not isinstance(info, dict):
        return None
    f = Fundamentals(
        symbol=sym.code,
        market=sym.market.value,
        name=str(info.get("longName") or info.get("shortName") or ""),
        pe_ttm=_num(info.get("trailingPE")),
        pb=_num(info.get("priceToBook")),
        ps_ttm=_num(info.get("priceToSalesTrailing12Months")),
        total_market_value=_per_100m(info.get("marketCap")),
        # yfinance >= 0.2.54 reports dividendYield already in percent (0.4 = 0.4%).
        dividend_yield=_num(info.get("dividendYield")),
        total_shares=_num(info.get("sharesOutstanding")),
        float_shares=_num(info.get("floatShares")),
        eps=_num(info.get("trailingEps")),
        bps=_num(info.get("bookValue")),
        roe=_pct(info.get("returnOnEquity")),
        revenue=_num(info.get("totalRevenue")),
        net_profit=_num(info.get("netIncomeToCommon")),
        gross_margin=_pct(info.get("grossMargins")),
        net_margin=_pct(info.get("profitMargins")),
        revenue_yoy=_pct(info.get("revenueGrowth")),
        net_profit_yoy=_pct(info.get("earningsGrowth")),
        report_date=_epoch_to_date(info.get("mostRecentQuarter")),
    )
    if f.pe_ttm is None and f.total_market_value is None and f.eps is None and f.revenue is None:
        return None
    return f


class YFinanceFundamentalsVendor(FundamentalsVendor):
    name = "yfinance"
    supports_markets = {"HK", "US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Fundamentals]:
        if not symbols:
            return []
        yf = _import_yf()
        out: list[Fundamentals] = []
        for s in symbols:
            ticker = _yf_ticker(s)
            try:
                f = info_to_fundamentals(s, yf.Ticker(ticker).info)
            except Exception as e:
                record_error(f"yfinance {ticker}: {type(e).__name__}: {e}")
                continue
            if f is None:
                record_error(f"yfinance {ticker}: no fundamentals returned")
                continue
            out.append(f)
        return out


class YFinanceDividendVendor(DividendVendor):
    """Cash dividend history (ex-date and amount per share, in the listing currency)."""

    name = "yfinance"
    supports_markets = {"HK", "US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[DividendItem]:
        if not symbols:
            return []
        yf = _import_yf()
        limit = int((config or {}).get("limit") or 20)
        out: list[DividendItem] = []
        for s in symbols:
            ticker = _yf_ticker(s)
            try:
                series = yf.Ticker(ticker).dividends
            except Exception as e:
                record_error(f"yfinance {ticker}: {type(e).__name__}: {e}")
                continue
            if series is None or len(series) == 0:
                continue
            # Newest first, matching the Eastmoney vendor's EX_DIVIDEND_DATE sort.
            for ts, amount in list(series.items())[::-1][:limit]:
                out.append(DividendItem(
                    ex_date=ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10],
                    symbol=s.code,
                    dividend_per_share=_num(amount),
                ))
        return out


def _as_date(value) -> date_type | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_type):
        return value
    return None


def _market_tz(sym: Symbol) -> ZoneInfo:
    return ZoneInfo("Asia/Hong_Kong" if sym.market == Market.HK else "America/New_York")


def calendar_to_events(sym: Symbol, calendar: dict, *, since: datetime) -> list[EventItem]:
    """Turn yfinance Ticker.calendar into dated events: next earnings and ex-dividend date.

    These are scheduled (often future) dates; publish_time is the event date at
    midnight exchange time, so they sort next to filings and announcements.
    """
    if not isinstance(calendar, dict):
        return []
    tz = _market_tz(sym)
    url = f"https://finance.yahoo.com/quote/{_yf_ticker(sym)}"
    out: list[EventItem] = []

    earnings = calendar.get("Earnings Date") or []
    if not isinstance(earnings, (list, tuple)):
        earnings = [earnings]
    dates = [d for d in (_as_date(v) for v in earnings) if d]
    if dates:
        day = min(dates)
        eps = _num(calendar.get("Earnings Average"))
        window = f"{day.isoformat()}–{max(dates).isoformat()}" if len(dates) > 1 else day.isoformat()
        title = f"Earnings report expected {window}"
        if eps is not None:
            title += f" (EPS estimate {eps:.2f})"
        out.append(EventItem(
            source="yfinance", external_id=f"{sym.code}:earnings:{day.isoformat()}",
            event_type="earnings", title=title,
            publish_time=datetime.combine(day, datetime.min.time(), tzinfo=tz),
            symbols=[sym.code], importance=3, url=url,
        ))

    ex_div = _as_date(calendar.get("Ex-Dividend Date"))
    if ex_div:
        pay = _as_date(calendar.get("Dividend Date"))
        title = f"Ex-dividend date {ex_div.isoformat()}" + (f", paid {pay.isoformat()}" if pay else "")
        out.append(EventItem(
            source="yfinance", external_id=f"{sym.code}:ex_dividend:{ex_div.isoformat()}",
            event_type="dividend", title=title,
            publish_time=datetime.combine(ex_div, datetime.min.time(), tzinfo=tz),
            symbols=[sym.code], importance=2, url=url,
        ))
    return [e for e in out if e.publish_time >= since]


class YFinanceCalendarVendor(EventsVendorBase):
    name = "yfinance"
    supports_markets = {"HK", "US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[EventItem]:
        if not symbols:
            return []
        yf = _import_yf()
        since_days = int((config or {}).get("since_days") or 7)
        since = datetime.now(timezone.utc) - timedelta(days=max(since_days, 1))
        out: list[EventItem] = []
        for s in symbols:
            ticker = _yf_ticker(s)
            try:
                cal = yf.Ticker(ticker).calendar
            except Exception as e:
                record_error(f"yfinance {ticker}: {type(e).__name__}: {e}")
                continue
            out.extend(calendar_to_events(s, cal, since=since))
        return out
