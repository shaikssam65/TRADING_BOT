from __future__ import annotations

import uuid
from datetime import datetime, timezone

from bot.config import Settings
from bot.models import (
    BotState,
    PendingApproval,
    Position,
    PositionAdvice,
    ProposedTrade,
)
from bot.runner import TradingBot, apply_capital
from bot.state import load_state, save_state


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def make_bot(settings: Settings) -> TradingBot:
    bot = TradingBot(settings)
    bot.connect_data()
    return bot


def refresh_marks(settings: Settings) -> BotState:
    """Reload the saved book and update live P&L. Safe to run every day."""
    state = load_state()
    if not state.positions:
        return state
    try:
        bot = make_bot(settings)
    except Exception:
        return state
    now = _now()
    for pos in state.positions:
        try:
            quote = bot.market.quote(pos.symbol) if bot.market else None
        except Exception:
            quote = None
        if quote is None:
            continue
        pos.last_price = quote.price
        pos.last_pnl = round((quote.price - pos.entry_price) * pos.shares, 2)
        pos.last_pnl_pct = round((quote.price / pos.entry_price - 1) * 100, 2) if pos.entry_price else 0.0
        pos.peak_price = max(pos.peak_price or pos.entry_price, quote.price)
        pos.last_mark_at = now
    save_state(state)
    return state


def trade_to_pending(trade: ProposedTrade, kind: str = "buy") -> PendingApproval:
    return PendingApproval(
        id=_new_id(),
        kind=kind,  # type: ignore[arg-type]
        symbol=trade.symbol,
        mode=trade.mode,
        shares=trade.shares,
        notional=trade.notional,
        entry=trade.entry,
        stop=trade.stop,
        take_profit=trade.take_profit,
        horizon_days=trade.horizon_days,
        expected_days=trade.expected_days,
        expected_price=trade.expected_price,
        expected_return_pct=trade.expected_return_pct,
        why_buy=trade.why_buy,
        why_sell=trade.why_sell,
        reasoning=trade.reasoning,
        risk_notes=trade.risk_notes,
        confidence=trade.confidence,
        risk_score=trade.risk_score,
        created_at=_now(),
        status="pending",
        vetoed=trade.vetoed,
        veto_reason=trade.veto_reason,
        name=trade.name,
        bullets=list(trade.bullets or []),
    )


def pending_to_trade(item: PendingApproval) -> ProposedTrade:
    return ProposedTrade(
        symbol=item.symbol,
        mode=item.mode,
        action="ADD" if item.kind == "add" else ("BUY" if item.kind == "buy" else "SELL"),
        shares=item.shares,
        notional=item.notional,
        entry=item.entry,
        stop=item.stop,
        take_profit=item.take_profit,
        horizon_days=item.horizon_days,
        why_buy=item.why_buy,
        why_sell=item.why_sell,
        risk_notes=item.risk_notes,
        confidence=item.confidence,
        risk_score=item.risk_score,
        vetoed=item.vetoed,
        veto_reason=item.veto_reason,
        expected_days=item.expected_days,
        expected_price=item.expected_price,
        expected_return_pct=item.expected_return_pct,
        reasoning=item.reasoning,
        name=item.name,
        bullets=list(item.bullets or []),
    )


def suggest_and_queue(settings: Settings) -> tuple[BotState, list[ProposedTrade], str]:
    """Scan, AI-rank, size, and store BUY ideas as pending human approvals."""
    state = load_state()
    state = apply_capital(state, settings, capital_override=True)
    bot = make_bot(settings)
    snaps = bot.scan()
    ranked = bot.advise(snaps)
    proposals = bot.propose(state, ranked)
    state.pending = [
        p for p in state.pending if p.kind in {"sell", "add"} or p.status == "approved"
    ]
    for trade in proposals:
        state.pending.append(trade_to_pending(trade, "buy"))
    save_state(state)
    return state, proposals, bot.advisor.provider


def set_pending_status(ids: list[str], status: str) -> BotState:
    state = load_state()
    wanted = set(ids)
    for item in state.pending:
        if item.id in wanted and item.status in {"pending", "approved"}:
            item.status = status  # type: ignore[assignment]
    save_state(state)
    return state


def execute_approved(settings: Settings) -> tuple[BotState, list[str]]:
    """Place only human-approved, non-vetoed orders. Never auto-increases capital."""
    state = load_state()
    messages: list[str] = []
    settings.execute = True
    bot = make_bot(settings)
    try:
        bot.connect_broker()
    except RuntimeError:
        if settings.live:
            raise
        from bot.broker.paper import PaperBroker

        bot.broker = PaperBroker()
        messages.append("No Webull API keys; orders filled in the local paper book only.")

    buy_items = [
        p
        for p in state.pending
        if p.kind in {"buy", "add"} and p.status == "approved" and not p.vetoed and p.shares > 0
    ]
    if buy_items:
        before = {p.symbol: p.shares for p in state.positions}
        trades = [pending_to_trade(p) for p in buy_items]
        state = bot.execute_buys(state, trades)
        after = {p.symbol: p.shares for p in state.positions}
        for item in buy_items:
            if after.get(item.symbol, 0) > before.get(item.symbol, 0):
                item.status = "executed"
                messages.append(f"Bought {item.shares} {item.symbol}")
            else:
                messages.append(f"Buy not filled for {item.symbol}")

    sell_items = [
        p for p in state.pending if p.kind == "sell" and p.status == "approved"
    ]
    remaining = list(state.positions)
    kept: list[Position] = []
    sold_symbols: set[str] = set()
    for pos in remaining:
        match = next((s for s in sell_items if s.symbol == pos.symbol), None)
        if not match:
            kept.append(pos)
            continue
        allowed, block = bot.risk.can_sell(state, pos, match.engine_reason or "approved")
        if not allowed:
            messages.append(f"Could not sell {pos.symbol}: {block}")
            kept.append(pos)
            continue
        sell = bot.broker.sell_market(pos.symbol, pos.shares)
        if not sell.ok:
            messages.append(f"SELL failed {pos.symbol}: {sell.message}")
            kept.append(pos)
            continue
        if pos.stop_order_id:
            bot.broker.cancel(pos.stop_order_id)
        try:
            px = bot.market.quote(pos.symbol).price if bot.market else pos.entry_price
        except Exception:
            px = pos.entry_price
        exit_px = sell.filled_price or px
        pnl = bot.risk.close_position(state, pos, exit_px, reason=match.engine_reason or "approved")
        match.status = "executed"
        sold_symbols.add(pos.symbol)
        messages.append(f"Sold {pos.shares} {pos.symbol} @ {exit_px:.2f} pnl={pnl:+.2f}")
    state.positions = kept
    save_state(state)
    return state, messages


def monitor_positions(settings: Settings) -> tuple[BotState, list[PositionAdvice], str]:
    state = load_state()
    if not state.positions:
        return state, [], "n/a"
    bot = make_bot(settings)
    advice: list[PositionAdvice] = []
    for pos in state.positions:
        try:
            snap = bot.market.snapshot(pos.symbol) if bot.market else None
        except Exception:
            snap = None
        if snap is None:
            continue
        pos.peak_price = max(pos.peak_price or pos.entry_price, snap.quote.price)
        news_kill = bot.market.severe_negative_news(snap.news) if bot.market else None
        engine_reason = bot.risk.exit_reason(pos, snap.quote.price, news_kill)
        decision = bot.advisor.review_position(
            pos, snap, engine_reason, pos.horizon_days, pos.mode
        )
        pnl = (snap.quote.price - pos.entry_price) * pos.shares
        pnl_pct = (snap.quote.price / pos.entry_price - 1) * 100 if pos.entry_price else 0
        advice.append(
            PositionAdvice(
                symbol=pos.symbol,
                price=snap.quote.price,
                pnl=round(pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                action=decision.action,
                confidence=decision.confidence,
                expected_days=decision.expected_days,
                expected_price=decision.expected_price,
                expected_return_pct=decision.expected_return_pct,
                reasoning=decision.reasoning,
                why_sell=decision.why_sell,
                risk_notes=decision.risk_notes,
                risk_score=decision.risk_score,
                engine_reason=engine_reason,
                urgent=bool(engine_reason),
                headlines=[n.headline for n in snap.news[:3] if n.headline],
                bullets=list(decision.bullets or []),
                add_shares=0,
            )
        )
        if advice[-1].action == "ADD":
            shares, reason = bot.risk.size_add(state, pos, snap.quote.price)
            advice[-1].add_shares = shares
            if shares < 1:
                advice[-1].action = "HOLD"
                advice[-1].bullets = (advice[-1].bullets or [])[:3] + [
                    reason or "Not enough cash to add."
                ]
        pos.last_price = snap.quote.price
        pos.last_pnl = round(pnl, 2)
        pos.last_pnl_pct = round(pnl_pct, 2)
        pos.last_mark_at = _now()
    state.last_advice = [a.__dict__ for a in advice]
    save_state(state)
    return state, advice, bot.advisor.provider


def queue_sells(advice_list: list[PositionAdvice], symbols: list[str]) -> BotState:
    state = load_state()
    wanted = {s.upper() for s in symbols}
    by_symbol = {a.symbol.upper(): a for a in advice_list}
    state.pending = [
        p
        for p in state.pending
        if not (p.kind == "sell" and p.symbol.upper() in wanted and p.status == "pending")
    ]
    for pos in state.positions:
        if pos.symbol.upper() not in wanted:
            continue
        adv = by_symbol.get(pos.symbol.upper())
        if adv is None:
            continue
        state.pending.append(
            PendingApproval(
                id=_new_id(),
                kind="sell",
                symbol=pos.symbol,
                mode=pos.mode,
                shares=pos.shares,
                notional=round(pos.shares * adv.price, 2),
                entry=pos.entry_price,
                stop=pos.stop_price,
                take_profit=pos.take_profit,
                horizon_days=pos.horizon_days,
                expected_days=adv.expected_days,
                expected_price=adv.expected_price,
                expected_return_pct=adv.expected_return_pct,
                why_buy=pos.why_buy,
                why_sell=adv.why_sell,
                reasoning=adv.reasoning,
                risk_notes=adv.risk_notes,
                confidence=adv.confidence,
                risk_score=adv.risk_score,
                created_at=_now(),
                status="pending",
                name=pos.symbol,
                urgent=adv.urgent,
                engine_reason=adv.engine_reason or "",
                bullets=list(adv.bullets or []),
            )
        )
    save_state(state)
    return state


def queue_adds(advice_list: list[PositionAdvice], symbols: list[str]) -> BotState:
    state = load_state()
    wanted = {s.upper() for s in symbols}
    by_symbol = {a.symbol.upper(): a for a in advice_list}
    state.pending = [
        p
        for p in state.pending
        if not (p.kind == "add" and p.symbol.upper() in wanted and p.status == "pending")
    ]
    for pos in state.positions:
        if pos.symbol.upper() not in wanted:
            continue
        adv = by_symbol.get(pos.symbol.upper())
        if adv is None or adv.add_shares < 1:
            continue
        state.pending.append(
            PendingApproval(
                id=_new_id(),
                kind="add",
                symbol=pos.symbol,
                mode=pos.mode,
                shares=adv.add_shares,
                notional=round(adv.add_shares * adv.price, 2),
                entry=adv.price,
                stop=pos.stop_price,
                take_profit=pos.take_profit,
                horizon_days=pos.horizon_days,
                expected_days=adv.expected_days,
                expected_price=adv.expected_price,
                expected_return_pct=adv.expected_return_pct,
                why_buy="Buy more of this holding.",
                why_sell=adv.why_sell,
                reasoning=adv.reasoning,
                risk_notes=adv.risk_notes,
                confidence=adv.confidence,
                risk_score=adv.risk_score,
                created_at=_now(),
                status="pending",
                name=pos.symbol,
                urgent=False,
                engine_reason="add",
                bullets=list(adv.bullets or []),
            )
        )
    save_state(state)
    return state
