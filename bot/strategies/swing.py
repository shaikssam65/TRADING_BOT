from __future__ import annotations

from bot.config import RiskLimits
from bot.models import Snapshot

# Larger / higher-quality US names for ~1 month holds.
SWING_UNIVERSE = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "AVGO",
    "JPM",
    "V",
    "UNH",
    "LLY",
    "XOM",
    "COST",
    "HD",
    "MA",
    "NFLX",
    "AMD",
    "TSLA",
    "ORCL",
    "CRM",
    "QCOM",
    "WMT",
    "PG",
    "CAT",
    "GE",
    "KO",
    "PEP",
    "ABBV",
    "LIN",
    "ADBE",
]


def score_swing(snap: Snapshot, limits: RiskLimits) -> Snapshot:
    notes: list[str] = []
    score = 0.0
    price = snap.quote.price

    if price < limits.min_price:
        snap.screen_score = -100
        snap.screen_notes = [f"Below ${limits.min_price:.0f} price floor"]
        return snap
    if snap.avg_volume and snap.avg_volume < limits.min_avg_volume:
        snap.screen_score = -100
        snap.screen_notes = ["Volume too thin"]
        return snap

    cap = snap.profile.market_cap if snap.profile else 0
    if cap >= 50_000_000_000:
        score += 2
        notes.append("Large / established market cap")
    elif cap >= 10_000_000_000:
        score += 1
        notes.append("Mid-large cap")
    elif cap and cap < 2_000_000_000:
        score -= 2
        notes.append("Too small for the 1-month book")

    if snap.above_sma50:
        score += 2
        notes.append("Above 50-day average")
    else:
        score -= 1.5
        notes.append("Below 50-day average")

    if snap.above_sma20:
        score += 1

    if 1 <= snap.rs_20d <= 15:
        score += 2
        notes.append(f"Steady 20d trend {snap.rs_20d:.1f}%")
    elif snap.rs_20d > 20:
        score += 0.5
        notes.append("Extended 20d move")
    elif snap.rs_20d < -8:
        score -= 2
        notes.append("20d trend is down")

    metrics = snap.metrics
    if metrics and metrics.revenue_growth is not None:
        if metrics.revenue_growth > 0.08:
            score += 1.5
            notes.append("Revenue growing")
        elif metrics.revenue_growth < -0.05:
            score -= 1.5
            notes.append("Revenue shrinking")

    if metrics and metrics.roe is not None and metrics.roe > 0.12:
        score += 1
        notes.append("Solid ROE")

    if metrics and metrics.debt_to_equity is not None and metrics.debt_to_equity > 2.5:
        score -= 1
        notes.append("High leverage")

    sent = snap.sentiment.news_score if snap.sentiment and snap.sentiment.news_score is not None else 0.0
    if sent >= 0.1:
        score += 1
        notes.append("News sentiment not hostile")
    elif sent <= -0.3:
        score -= 2
        notes.append("Negative news tape")

    if snap.earnings_in_days is not None and snap.earnings_in_days <= 2:
        score -= 4
        notes.append("Earnings too close")

    if metrics and metrics.week_52_high and price:
        dist = price / metrics.week_52_high
        if 0.85 <= dist <= 1.02:
            score += 0.5
            notes.append("Near 52-week high (trend)")
        elif dist < 0.6:
            score -= 1
            notes.append("Far below 52-week high")

    snap.screen_score = round(score, 2)
    snap.screen_notes = notes
    return snap
