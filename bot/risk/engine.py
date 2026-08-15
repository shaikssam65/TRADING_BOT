from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import floor

from bot.config import Settings
from bot.models import (
    AiDecision,
    BotState,
    ClosedTrade,
    DayTrade,
    Position,
    ProposedTrade,
    Snapshot,
)


class RiskEngine:
    """Hard limits AI cannot override."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.limits = settings.risk

    def reset_daily_if_needed(self, state: BotState, equity: float) -> BotState:
        today = date.today().isoformat()
        if state.daily_start_date != today:
            state.daily_start_date = today
            state.daily_start_equity = equity
            state.kill_switch = False
            state.kill_reason = ""
        return state

    def mark_to_market(self, state: BotState, prices: dict[str, float]) -> float:
        invested_now = 0.0
        for pos in state.positions:
            px = prices.get(pos.symbol, pos.entry_price)
            invested_now += pos.shares * px
        cash_used = state.invested()
        cash = max(0.0, state.allocated_capital - cash_used + state.realized_pnl)
        return cash + invested_now

    def apply_kill_switch(self, state: BotState, equity: float) -> BotState:
        start = state.daily_start_equity or state.allocated_capital
        if start <= 0:
            return state
        drawdown = (start - equity) / start
        if drawdown >= self.limits.daily_kill_pct:
            state.kill_switch = True
            state.kill_reason = (
                f"Daily kill switch: allocated book is down {drawdown:.1%} "
                f"(limit {self.limits.daily_kill_pct:.1%}). No new buys."
            )
        return state

    def day_trades_5d(self, state: BotState, today: date | None = None) -> list[DayTrade]:
        today = today or date.today()
        cutoff = today - timedelta(days=7)
        kept: list[DayTrade] = []
        for item in state.day_trades:
            try:
                d = date.fromisoformat(item.date)
            except ValueError:
                continue
            if d >= cutoff:
                kept.append(item)
        return kept

    def would_be_day_trade(self, state: BotState, symbol: str, today: date | None = None) -> bool:
        today = today or date.today()
        for pos in state.positions:
            if pos.symbol == symbol and pos.opened_at[:10] == today.isoformat():
                return True
        return False

    def can_open(self, state: BotState) -> tuple[bool, str]:
        if state.kill_switch:
            return False, state.kill_reason or "Kill switch is on."
        if len(state.positions) >= self.limits.max_positions:
            return False, f"Max open positions is {self.limits.max_positions}."
        buffer = state.allocated_capital * self.limits.cash_buffer_pct
        if state.cash() <= buffer:
            return False, "Cash buffer would be breached; no new buys."
        if len(self.day_trades_5d(state)) >= self.limits.max_day_trades_5d:
            return False, "Pattern-day-trader guard: too many day trades in 5 days."
        return True, ""

    def can_sell(self, state: BotState, pos: Position, reason: str) -> tuple[bool, str]:
        today = date.today()
        age = pos.age_days(today)
        forced = reason in {"stop", "news", "kill", "gap"}
        if age < self.settings.min_hold_days and not forced:
            return False, (
                f"Min hold is {self.settings.min_hold_days} days to avoid PDT "
                f"(position age {age}d). Forced exits still allowed."
            )
        if self.would_be_day_trade(state, pos.symbol, today) and not forced:
            return False, "Same-day round trip blocked (PDT guard)."
        return True, ""

    def size_trade(self, state: BotState, snap: Snapshot, decision: AiDecision) -> ProposedTrade:
        price = snap.quote.price
        stop_pct = self.settings.stop_pct
        tp_pct = self.settings.take_profit_pct
        stop = round(price * (1 - stop_pct), 4)
        take_profit = round(price * (1 + tp_pct), 4)
        trade = ProposedTrade(
            symbol=snap.symbol,
            mode=self.settings.mode,
            action=decision.action,
            shares=0,
            notional=0.0,
            entry=price,
            stop=stop,
            take_profit=take_profit,
            horizon_days=decision.horizon_days,
            why_buy=decision.why_buy,
            why_sell=decision.why_sell,
            risk_notes=decision.risk_notes,
            confidence=decision.confidence,
            risk_score=decision.risk_score,
            expected_days=decision.expected_days or decision.horizon_days,
            expected_price=decision.expected_price,
            expected_return_pct=decision.expected_return_pct,
            reasoning=decision.reasoning or decision.why_buy,
        )

        if decision.action not in {"BUY", "ADD"}:
            trade.vetoed = True
            trade.veto_reason = f"AI action is {decision.action}, not BUY."
            return trade

        existing = next((p for p in state.positions if p.symbol == snap.symbol), None)
        adding = decision.action == "ADD" or existing is not None

        if not adding:
            ok, reason = self.can_open(state)
            if not ok:
                trade.vetoed = True
                trade.veto_reason = reason
                return trade
        elif existing is None:
            ok, reason = self.can_open(state)
            if not ok:
                trade.vetoed = True
                trade.veto_reason = reason
                return trade

        if existing is not None and decision.action != "ADD":
            trade.vetoed = True
            trade.veto_reason = "Already holding this symbol."
            return trade

        if price < self.limits.min_price:
            trade.vetoed = True
            trade.veto_reason = f"Price ${price:.2f} is below ${self.limits.min_price:.2f}."
            return trade

        if snap.avg_volume and snap.avg_volume < self.limits.min_avg_volume:
            trade.vetoed = True
            trade.veto_reason = "Average volume is below the liquidity floor."
            return trade

        if decision.action != "ADD" and decision.confidence < self.limits.min_buy_confidence:
            trade.vetoed = True
            trade.veto_reason = (
                f"Confidence {decision.confidence:.2f} is below {self.limits.min_buy_confidence:.2f}."
            )
            return trade

        if decision.risk_score > self.limits.max_risk_score:
            trade.vetoed = True
            trade.veto_reason = f"Risk score {decision.risk_score} exceeds {self.limits.max_risk_score}."
            return trade

        if snap.earnings_in_days is not None and snap.earnings_in_days <= 2:
            trade.vetoed = True
            trade.veto_reason = "Earnings within 2 days."
            return trade

        cap = state.allocated_capital
        max_notional = cap * self.limits.max_position_pct
        buffer = cap * self.limits.cash_buffer_pct
        room = max(0.0, state.cash() - buffer)
        already = existing.shares * existing.entry_price if existing else 0.0
        name_room = max(0.0, max_notional - already)
        notional = min(max_notional if not existing else name_room, room)
        if existing:
            notional = min(notional, max_notional * 0.5)
        if notional < price:
            trade.vetoed = True
            trade.veto_reason = "Not enough allocated cash for even 1 share after the cash buffer."
            return trade

        shares = floor(notional / price)
        if shares < 1:
            trade.vetoed = True
            trade.veto_reason = "Position size rounded to 0 shares."
            return trade

        trade.shares = shares
        trade.notional = round(shares * price, 2)
        return trade

    def size_add(self, state: BotState, pos: Position, price: float) -> tuple[int, str]:
        if price <= 0:
            return 0, "No price."
        buffer = state.allocated_capital * self.limits.cash_buffer_pct
        room = max(0.0, state.cash() - buffer)
        already = pos.shares * pos.entry_price
        max_notional = state.allocated_capital * self.limits.max_position_pct
        name_room = max(0.0, max_notional - already)
        notional = min(name_room, room, max_notional * 0.5)
        if notional < price:
            return 0, "Not enough allocated cash to buy more."
        return floor(notional / price), ""

    def exit_reason(
        self,
        pos: Position,
        price: float,
        news_kill: str | None,
        today: date | None = None,
    ) -> str | None:
        today = today or date.today()
        if news_kill:
            return "news"
        if price <= pos.stop_price:
            return "stop"
        peak = max(pos.peak_price or pos.entry_price, price)
        activated = peak >= pos.entry_price * (1 + self.limits.trail_activate_pct)
        if activated:
            trail_stop = peak * (1 - self.limits.trail_pct)
            if price <= trail_stop:
                return "trail"
        if price >= pos.take_profit:
            return "take_profit"
        if pos.age_days(today) >= pos.horizon_days:
            return "time"
        return None

    def record_day_trade_if_needed(self, state: BotState, pos: Position) -> None:
        opened = pos.opened_at[:10]
        today = date.today().isoformat()
        if opened == today:
            state.day_trades.append(DayTrade(date=today, symbol=pos.symbol))

    def close_position(
        self, state: BotState, pos: Position, exit_price: float, reason: str = ""
    ) -> float:
        pnl = (exit_price - pos.entry_price) * pos.shares
        pct = ((exit_price / pos.entry_price) - 1) * 100 if pos.entry_price else 0.0
        state.realized_pnl += pnl
        self.record_day_trade_if_needed(state, pos)
        state.closed_trades.append(
            ClosedTrade(
                symbol=pos.symbol,
                shares=pos.shares,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                pnl=round(pnl, 2),
                pnl_pct=round(pct, 2),
                opened_at=pos.opened_at,
                closed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                reason=reason,
            )
        )
        return pnl
