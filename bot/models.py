from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Mode = Literal["short", "swing"]
Action = Literal["BUY", "HOLD", "AVOID", "SELL", "ADD"]
Side = Literal["BUY", "SELL"]
PriceFilter = Literal["under_20", "20_50", "50_100", "top20"]
PendingKind = Literal["buy", "sell", "add"]
PendingStatus = Literal["pending", "approved", "rejected", "executed"]


@dataclass
class Quote:
    symbol: str
    price: float
    change_pct: float
    high: float
    low: float
    open: float
    prev_close: float
    timestamp: int = 0


@dataclass
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class NewsItem:
    headline: str
    summary: str
    source: str
    datetime: int
    url: str = ""


@dataclass
class Sentiment:
    symbol: str
    news_score: float | None = None
    social_score: float | None = None
    buzz: float | None = None


@dataclass
class Profile:
    symbol: str
    name: str = ""
    market_cap: float = 0.0
    industry: str = ""
    exchange: str = ""
    shares_outstanding: float = 0.0


@dataclass
class Metrics:
    symbol: str
    pe: float | None = None
    eps: float | None = None
    week_52_high: float | None = None
    week_52_low: float | None = None
    avg_volume_10d: float | None = None
    revenue_growth: float | None = None
    debt_to_equity: float | None = None
    roe: float | None = None


@dataclass
class Snapshot:
    symbol: str
    quote: Quote
    bars: list[Bar] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    sentiment: Sentiment | None = None
    profile: Profile | None = None
    metrics: Metrics | None = None
    earnings_in_days: int | None = None
    rs_10d: float = 0.0
    rs_20d: float = 0.0
    avg_volume: float = 0.0
    volume_ratio: float = 1.0
    above_sma20: bool = False
    above_sma50: bool = False
    screen_score: float = 0.0
    screen_notes: list[str] = field(default_factory=list)
    ml_expected_return_pct: float | None = None
    ml_horizon_days: int | None = None
    ml_confidence: float | None = None


@dataclass
class AiDecision:
    ticker: str
    action: Action
    confidence: float
    horizon_days: int
    why_buy: str
    why_sell: str
    risk_notes: str
    risk_score: int
    expected_days: int = 0
    expected_price: float = 0.0
    expected_return_pct: float = 0.0
    reasoning: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class ProposedTrade:
    symbol: str
    mode: Mode
    action: Action
    shares: int
    notional: float
    entry: float
    stop: float
    take_profit: float
    horizon_days: int
    why_buy: str
    why_sell: str
    risk_notes: str
    confidence: float
    risk_score: int
    vetoed: bool = False
    veto_reason: str = ""
    expected_days: int = 0
    expected_price: float = 0.0
    expected_return_pct: float = 0.0
    reasoning: str = ""
    name: str = ""
    headlines: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


@dataclass
class Position:
    symbol: str
    mode: Mode
    shares: int
    entry_price: float
    stop_price: float
    take_profit: float
    opened_at: str
    horizon_days: int
    why_buy: str = ""
    why_sell: str = ""
    client_order_id: str = ""
    stop_order_id: str = ""
    peak_price: float = 0.0
    reasoning: str = ""
    expected_price: float = 0.0
    expected_days: int = 0
    last_price: float = 0.0
    last_pnl: float = 0.0
    last_pnl_pct: float = 0.0
    last_mark_at: str = ""

    def age_days(self, today: date | None = None) -> int:
        opened = date.fromisoformat(self.opened_at[:10])
        return ((today or date.today()) - opened).days


@dataclass
class DayTrade:
    date: str
    symbol: str


@dataclass
class PendingApproval:
    id: str
    kind: PendingKind
    symbol: str
    mode: Mode
    shares: int
    notional: float
    entry: float
    stop: float
    take_profit: float
    horizon_days: int
    expected_days: int
    expected_price: float
    expected_return_pct: float
    why_buy: str
    why_sell: str
    reasoning: str
    risk_notes: str
    confidence: float
    risk_score: int
    created_at: str
    status: PendingStatus = "pending"
    vetoed: bool = False
    veto_reason: str = ""
    name: str = ""
    urgent: bool = False
    engine_reason: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class PositionAdvice:
    symbol: str
    price: float
    pnl: float
    pnl_pct: float
    action: Action
    confidence: float
    expected_days: int
    expected_price: float
    expected_return_pct: float
    reasoning: str
    why_sell: str
    risk_notes: str
    risk_score: int
    engine_reason: str | None = None
    urgent: bool = False
    headlines: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    add_shares: int = 0


@dataclass
class ClosedTrade:
    symbol: str
    shares: int
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    opened_at: str
    closed_at: str
    reason: str = ""


@dataclass
class BotState:
    allocated_capital: float = 1000.0
    mode: Mode = "short"
    positions: list[Position] = field(default_factory=list)
    day_trades: list[DayTrade] = field(default_factory=list)
    pending: list[PendingApproval] = field(default_factory=list)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    last_advice: list[dict] = field(default_factory=list)
    daily_start_equity: float = 1000.0
    daily_start_date: str = ""
    kill_switch: bool = False
    kill_reason: str = ""
    realized_pnl: float = 0.0
    updated_at: str = ""

    def invested(self) -> float:
        return sum(p.shares * p.entry_price for p in self.positions)

    def cash(self) -> float:
        return max(0.0, self.allocated_capital - self.invested() + self.realized_pnl)
