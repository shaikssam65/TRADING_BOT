from __future__ import annotations

from dataclasses import dataclass

from bot.config import Settings
from bot.data.finnhub_client import FinnhubClient
from bot.data.webull_market import WebullMarketData
from bot.models import Bar, Metrics, NewsItem, Profile, Quote, Sentiment, Snapshot


@dataclass
class MarketData:
    finnhub: FinnhubClient
    webull: WebullMarketData | None = None

    def quote(self, symbol: str) -> Quote:
        if self.webull and self.webull.available:
            wb = self.webull.quote(symbol)
            if wb is not None:
                return wb
        return self.finnhub.quote(symbol)

    def candles(self, symbol: str, days: int = 60) -> list[Bar]:
        if self.webull and self.webull.available:
            bars = self.webull.candles(symbol, days=days)
            if bars:
                return bars
        return self.finnhub.candles(symbol, days=days)

    def price_snapshot(self, symbol: str) -> Snapshot:
        quote = self.quote(symbol)
        bars = self.candles(symbol, days=70)
        rs_10d, rs_20d, avg_volume, volume_ratio, above_sma20, above_sma50 = _from_bars(
            bars, quote.price
        )
        return Snapshot(
            symbol=symbol,
            quote=quote,
            bars=bars,
            rs_10d=rs_10d,
            rs_20d=rs_20d,
            avg_volume=avg_volume,
            volume_ratio=volume_ratio,
            above_sma20=above_sma20,
            above_sma50=above_sma50,
        )

    def enrich(self, snap: Snapshot, news_days: int = 7) -> Snapshot:
        try:
            snap.news = self.finnhub.company_news(snap.symbol, days=news_days)
        except Exception:
            snap.news = []
        try:
            snap.sentiment = self.finnhub.news_sentiment_raw(snap.symbol)
        except Exception:
            snap.sentiment = Sentiment(symbol=snap.symbol)
        try:
            snap.profile = self.finnhub.profile(snap.symbol)
        except Exception:
            snap.profile = Profile(symbol=snap.symbol, name=snap.symbol)
        try:
            snap.metrics = self.finnhub.metrics(snap.symbol)
        except Exception:
            snap.metrics = Metrics(symbol=snap.symbol)
        try:
            snap.earnings_in_days = self.finnhub.earnings_in_days(snap.symbol)
        except Exception:
            snap.earnings_in_days = None
        if snap.metrics and snap.metrics.avg_volume_10d:
            metric_vol = snap.metrics.avg_volume_10d
            if metric_vol < 50_000:
                metric_vol *= 1_000_000
            snap.avg_volume = max(snap.avg_volume, metric_vol)
        return snap

    def snapshot(self, symbol: str, news_days: int = 7) -> Snapshot:
        return self.enrich(self.price_snapshot(symbol), news_days=news_days)

    def severe_negative_news(self, news: list[NewsItem]) -> str | None:
        return self.finnhub.severe_negative_news(news)


def build_market_data(settings: Settings) -> MarketData:
    finnhub = FinnhubClient(settings.finnhub_api_key)
    webull = None
    if settings.webull_app_key and settings.webull_app_secret:
        webull = WebullMarketData(
            settings.webull_app_key,
            settings.webull_app_secret,
            live=settings.live,
        )
    return MarketData(finnhub=finnhub, webull=webull)


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _from_bars(
    bars: list[Bar], last_price: float
) -> tuple[float, float, float, float, bool, bool]:
    if not bars:
        return 0.0, 0.0, 0.0, 1.0, False, False
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    rs_10d = 0.0
    rs_20d = 0.0
    if len(closes) >= 11 and closes[-11]:
        rs_10d = (closes[-1] / closes[-11] - 1) * 100
    if len(closes) >= 21 and closes[-21]:
        rs_20d = (closes[-1] / closes[-21] - 1) * 100
    avg_volume = sum(volumes[-10:]) / max(1, min(10, len(volumes)))
    last_volume = volumes[-1] if volumes else 0
    volume_ratio = (last_volume / avg_volume) if avg_volume else 1.0
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    above_sma20 = sma20 is not None and last_price >= sma20
    above_sma50 = sma50 is not None and last_price >= sma50
    return rs_10d, rs_20d, avg_volume, volume_ratio, above_sma20, above_sma50
