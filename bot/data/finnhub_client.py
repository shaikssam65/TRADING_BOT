from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from bot.models import Bar, Metrics, NewsItem, Profile, Quote, Sentiment

FINNHUB_BASE = "https://finnhub.io/api/v1"

NEWS_KILL_WORDS = (
    "bankruptcy",
    "bankrupt",
    "fraud",
    "sec charges",
    "sec charge",
    "criminal",
    "investigation",
    "going concern",
    "delist",
    "delisting",
    "restatement",
    "default on debt",
)


class FinnhubError(RuntimeError):
    pass


class FinnhubClient:
    def __init__(self, api_key: str, timeout: int = 20) -> None:
        if not api_key:
            raise FinnhubError(
                "FINNHUB_API_KEY is missing. Copy .env.example to .env and add a free key from https://finnhub.io"
            )
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._min_interval = 0.05
        self._last_call = 0.0

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        query = dict(params or {})
        query["token"] = self.api_key
        url = f"{FINNHUB_BASE}{path}"
        try:
            resp = self._session.get(url, params=query, timeout=self.timeout)
        except requests.RequestException as exc:
            raise FinnhubError(f"Finnhub request failed: {exc}") from exc
        self._last_call = time.monotonic()
        if resp.status_code == 429:
            time.sleep(1.2)
            resp = self._session.get(url, params=query, timeout=self.timeout)
            self._last_call = time.monotonic()
        if resp.status_code != 200:
            raise FinnhubError(f"Finnhub {path} HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def quote(self, symbol: str) -> Quote:
        data = self._get("/quote", {"symbol": symbol})
        price = float(data.get("c") or 0)
        if price <= 0:
            raise FinnhubError(f"No quote for {symbol}")
        return Quote(
            symbol=symbol,
            price=price,
            change_pct=float(data.get("dp") or 0),
            high=float(data.get("h") or 0),
            low=float(data.get("l") or 0),
            open=float(data.get("o") or 0),
            prev_close=float(data.get("pc") or 0),
            timestamp=int(data.get("t") or 0),
        )

    def candles(self, symbol: str, days: int = 60, resolution: str = "D") -> list[Bar]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 10)
        try:
            data = self._get(
                "/stock/candle",
                {
                    "symbol": symbol,
                    "resolution": resolution,
                    "from": int(start.timestamp()),
                    "to": int(end.timestamp()),
                },
            )
        except FinnhubError:
            return []
        if not isinstance(data, dict) or data.get("s") != "ok":
            return []
        closes = data.get("c") or []
        bars: list[Bar] = []
        for i, close in enumerate(closes):
            bars.append(
                Bar(
                    time=int(data["t"][i]),
                    open=float(data["o"][i]),
                    high=float(data["h"][i]),
                    low=float(data["l"][i]),
                    close=float(close),
                    volume=float(data["v"][i]),
                )
            )
        return bars

    def company_news(self, symbol: str, days: int = 7) -> list[NewsItem]:
        today = date.today()
        start = today - timedelta(days=days)
        data = self._get(
            "/company-news",
            {"symbol": symbol, "from": start.isoformat(), "to": today.isoformat()},
        )
        items: list[NewsItem] = []
        if not isinstance(data, list):
            return items
        for row in data[:12]:
            items.append(
                NewsItem(
                    headline=str(row.get("headline") or ""),
                    summary=str(row.get("summary") or "")[:400],
                    source=str(row.get("source") or ""),
                    datetime=int(row.get("datetime") or 0),
                    url=str(row.get("url") or ""),
                )
            )
        return items

    def news_sentiment(self, symbol: str) -> Sentiment:
        return self.news_sentiment_raw(symbol)

    def news_sentiment_raw(self, symbol: str) -> Sentiment:
        data = self._get("/news-sentiment", {"symbol": symbol})
        company = data.get("companyNewsScore")
        sector = data.get("sectorAverageNewsScore")
        buzz = _nested_float(data, "buzz", "buzz")
        twitter = _nested_float(data, "twitter", "score")
        # companyNewsScore is typically 0-1; map to -1..1 around 0.5
        news_score = None
        if company is not None:
            news_score = (float(company) - 0.5) * 2
            if sector is not None:
                news_score = news_score + (float(company) - float(sector))
        return Sentiment(
            symbol=symbol,
            news_score=news_score,
            social_score=twitter,
            buzz=buzz,
        )

    def profile(self, symbol: str) -> Profile:
        data = self._get("/stock/profile2", {"symbol": symbol})
        return Profile(
            symbol=symbol,
            name=str(data.get("name") or symbol),
            market_cap=float(data.get("marketCapitalization") or 0) * 1_000_000,
            industry=str(data.get("finnhubIndustry") or ""),
            exchange=str(data.get("exchange") or ""),
            shares_outstanding=float(data.get("shareOutstanding") or 0),
        )

    def metrics(self, symbol: str) -> Metrics:
        data = self._get("/stock/metric", {"symbol": symbol, "metric": "all"})
        m = data.get("metric") or {}
        return Metrics(
            symbol=symbol,
            pe=_to_float(m.get("peNormalizedAnnual") or m.get("peBasicExclExtraTTM")),
            eps=_to_float(m.get("epsNormalizedAnnual") or m.get("epsInclExtraItemsTTM")),
            week_52_high=_to_float(m.get("52WeekHigh")),
            week_52_low=_to_float(m.get("52WeekLow")),
            avg_volume_10d=_to_float(m.get("10DayAverageTradingVolume")),
            revenue_growth=_to_float(m.get("revenueGrowthTTMYoy") or m.get("revenueGrowthQuarterlyYoy")),
            debt_to_equity=_to_float(m.get("totalDebt/totalEquityAnnual") or m.get("netDebtToEquityAnnual")),
            roe=_to_float(m.get("roeRfy") or m.get("roeTTM")),
        )

    def earnings_in_days(self, symbol: str) -> int | None:
        today = date.today()
        end = today + timedelta(days=45)
        data = self._get(
            "/calendar/earnings",
            {"from": today.isoformat(), "to": end.isoformat(), "symbol": symbol},
        )
        rows = []
        if isinstance(data, dict):
            rows = data.get("earningsCalendar") or []
        nearest: int | None = None
        for row in rows:
            raw = row.get("date") or row.get("earningsDate")
            if not raw:
                continue
            try:
                earn_date = date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue
            delta = (earn_date - today).days
            if delta >= 0 and (nearest is None or delta < nearest):
                nearest = delta
        return nearest

    def severe_negative_news(self, news: list[NewsItem]) -> str | None:
        for item in news[:8]:
            text = f"{item.headline} {item.summary}".lower()
            for word in NEWS_KILL_WORDS:
                if word in text:
                    return f"Negative headline ({item.source}): {item.headline}"
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested_float(data: dict[str, Any], *keys: str) -> float | None:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return _to_float(cur)
