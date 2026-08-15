from __future__ import annotations

from bot.config import RiskLimits
from bot.models import Snapshot

# Liquid US names that can still move in ~10 days. Not penny stocks.
SHORT_UNIVERSE = [
    "SOFI",
    "HOOD",
    "RIVN",
    "SNAP",
    "RBLX",
    "AFRM",
    "DKNG",
    "U",
    "PATH",
    "IONQ",
    "MARA",
    "RIOT",
    "NIO",
    "JOBY",
    "RKLB",
    "ASTS",
    "HIMS",
    "CELH",
    "CVNA",
    "TOST",
    "SMCI",
    "ARM",
    "MU",
    "INTC",
    "AMD",
    "PLTR",
    "COIN",
    "CRWD",
    "SNOW",
    "NET",
]


def score_short(snap: Snapshot, limits: RiskLimits) -> Snapshot:
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

    if 3 <= snap.rs_10d <= 18:
        score += 3
        notes.append(f"Healthy 10d momentum {snap.rs_10d:.1f}%")
    elif snap.rs_10d > 18:
        score += 1
        notes.append("Momentum is stretched; easier to snap back")
    elif snap.rs_10d < -6:
        score -= 3
        notes.append("10d trend is down")
    else:
        score += 0.5

    if snap.above_sma20:
        score += 1.5
        notes.append("Above 20-day average")
    else:
        score -= 1
        notes.append("Below 20-day average")

    if snap.volume_ratio >= 1.4:
        score += 1.5
        notes.append("Volume surge")
    elif snap.volume_ratio < 0.7:
        score -= 0.5

    sent = snap.sentiment.news_score if snap.sentiment and snap.sentiment.news_score is not None else 0.0
    if sent >= 0.15:
        score += 1.5
        notes.append("Constructive news sentiment")
    elif sent <= -0.25:
        score -= 2
        notes.append("Negative news sentiment")

    if snap.earnings_in_days is not None and snap.earnings_in_days <= 2:
        score -= 5
        notes.append(f"Earnings in {snap.earnings_in_days}d")
    elif snap.earnings_in_days is not None and snap.earnings_in_days <= 7:
        score -= 1
        notes.append(f"Earnings in {snap.earnings_in_days}d")

    if snap.news:
        score += 0.5
        notes.append("Has recent coverage")

    cap = snap.profile.market_cap if snap.profile else 0
    if 1_000_000_000 <= cap <= 80_000_000_000:
        score += 0.5

    snap.screen_score = round(score, 2)
    snap.screen_notes = notes
    return snap
