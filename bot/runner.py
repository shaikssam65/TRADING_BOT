from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from bot.ai.advisor import Advisor
from bot.broker.factory import build_broker
from bot.config import Settings
from bot.data.market import MarketData, build_market_data
from bot.models import AiDecision, BotState, Position, ProposedTrade, Snapshot
from bot.risk.engine import RiskEngine
from bot.state import load_state, save_state
from bot.strategies.short import SHORT_UNIVERSE, score_short
from bot.strategies.swing import SWING_UNIVERSE, score_swing
from bot.strategies.universe import matches_filters, result_count, symbols_for_filters

console = Console()


def universe_for(mode: str) -> list[str]:
    return list(SWING_UNIVERSE if mode == "swing" else SHORT_UNIVERSE)


def _decorate_trade(trade: ProposedTrade, snap: Snapshot, decision: AiDecision) -> None:
    trade.name = snap.profile.name if snap.profile else snap.symbol
    trade.headlines = [n.headline for n in snap.news[:4] if n.headline]
    trade.expected_days = decision.expected_days or trade.expected_days
    trade.expected_price = decision.expected_price or trade.expected_price
    trade.expected_return_pct = decision.expected_return_pct
    trade.reasoning = decision.reasoning or trade.reasoning
    trade.bullets = list(decision.bullets or [])
    if not trade.bullets:
        from bot.textfmt import to_bullets

        trade.bullets = to_bullets(decision.why_buy, decision.why_sell, limit=4)


def score_snapshot(snap: Snapshot, settings: Settings) -> Snapshot:
    if settings.mode == "swing":
        return score_swing(snap, settings.risk)
    return score_short(snap, settings.risk)


def apply_capital(state: BotState, settings: Settings, capital_override: bool) -> BotState:
    if capital_override:
        state.allocated_capital = settings.capital
    else:
        settings.capital = state.allocated_capital or settings.capital
        state.allocated_capital = settings.capital
    state.mode = settings.mode
    return state


class TradingBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.market: MarketData | None = None
        self.advisor = Advisor(settings)
        self.risk = RiskEngine(settings)
        self.broker = None

    def connect_data(self) -> None:
        self.market = build_market_data(self.settings)

    def connect_broker(self):
        self.broker = build_broker(self.settings, force_paper=not self.settings.execute)
        return self.broker

    def scan(self, limit_enrich: int | None = None) -> list[Snapshot]:
        assert self.market is not None
        keep_n = result_count(self.settings.price_filters) if self.settings.price_filters else 10
        if limit_enrich is None:
            limit_enrich = keep_n
        if self.settings.price_filters:
            symbols = symbols_for_filters(self.settings.price_filters)
        else:
            symbols = universe_for(self.settings.mode)
        prelim: list[Snapshot] = []
        for symbol in symbols:
            try:
                snap = self.market.price_snapshot(symbol)
            except Exception as exc:
                console.print(f"[dim]skip {symbol}: {exc}[/dim]")
                continue
            if self.settings.price_filters and not matches_filters(
                symbol, snap.quote.price, self.settings.price_filters
            ):
                continue
            prelim.append(score_snapshot(snap, self.settings))
        prelim.sort(key=lambda s: s.screen_score, reverse=True)
        detailed: list[Snapshot] = []
        for snap in prelim[: max(limit_enrich, keep_n)]:
            if snap.screen_score <= -50:
                continue
            try:
                full = self.market.enrich(snap)
            except Exception:
                full = snap
            detailed.append(score_snapshot(full, self.settings))
        detailed.sort(key=lambda s: s.screen_score, reverse=True)
        return detailed[:keep_n]

    def advise(self, snaps: list[Snapshot]) -> list[tuple[Snapshot, AiDecision]]:
        out: list[tuple[Snapshot, AiDecision]] = []
        keep = result_count(self.settings.price_filters) if self.settings.price_filters else self.settings.top_n
        for snap in snaps[:keep]:
            decision = self.advisor.decide(
                snap, self.settings.horizon_days, self.settings.mode
            )
            out.append((snap, decision))
        return out

    def propose(
        self, state: BotState, ranked: list[tuple[Snapshot, AiDecision]]
    ) -> list[ProposedTrade]:
        proposals: list[ProposedTrade] = []
        working = replace(state, positions=list(state.positions))
        for snap, decision in ranked:
            news_kill = self.market.severe_negative_news(snap.news) if self.market else None
            if news_kill:
                trade = self.risk.size_trade(working, snap, decision)
                trade.vetoed = True
                trade.veto_reason = news_kill
                trade.action = "AVOID"
                _decorate_trade(trade, snap, decision)
                proposals.append(trade)
                continue
            trade = self.risk.size_trade(working, snap, decision)
            _decorate_trade(trade, snap, decision)
            proposals.append(trade)
            if not trade.vetoed and trade.shares > 0:
                # Reserve cash so later names do not over-allocate in this pass.
                working.positions = list(working.positions) + [
                    Position(
                        symbol=trade.symbol,
                        mode=trade.mode,
                        shares=trade.shares,
                        entry_price=trade.entry,
                        stop_price=trade.stop,
                        take_profit=trade.take_profit,
                        opened_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        horizon_days=trade.horizon_days,
                    )
                ]
        return proposals

    def execute_buys(self, state: BotState, trades: list[ProposedTrade]) -> BotState:
        assert self.broker is not None
        for trade in trades:
            if trade.vetoed or trade.shares < 1 or trade.action not in {"BUY", "ADD"}:
                continue
            buy = self.broker.buy_limit(trade.symbol, trade.shares, trade.entry)
            if not buy.ok:
                console.print(f"[red]BUY failed {trade.symbol}: {buy.message}[/red]")
                continue
            stop = self.broker.place_stop_sell(trade.symbol, trade.shares, trade.stop)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            fill = buy.filled_price or trade.entry
            existing = next((p for p in state.positions if p.symbol == trade.symbol), None)
            if existing:
                total = existing.shares + trade.shares
                existing.entry_price = (
                    existing.entry_price * existing.shares + fill * trade.shares
                ) / total
                existing.shares = total
                existing.peak_price = max(existing.peak_price or fill, fill)
                existing.stop_price = trade.stop or existing.stop_price
                existing.stop_order_id = stop.broker_order_id or existing.stop_order_id
                existing.last_price = fill
                existing.last_pnl = 0.0
                existing.last_pnl_pct = 0.0
                existing.last_mark_at = now
            else:
                state.positions.append(
                    Position(
                        symbol=trade.symbol,
                        mode=trade.mode,
                        shares=trade.shares,
                        entry_price=fill,
                        stop_price=trade.stop,
                        take_profit=trade.take_profit,
                        opened_at=now,
                        horizon_days=trade.horizon_days,
                        why_buy=trade.why_buy,
                        why_sell=trade.why_sell,
                        client_order_id=buy.client_order_id,
                        stop_order_id=stop.broker_order_id,
                        peak_price=fill,
                        reasoning=trade.reasoning,
                        expected_price=trade.expected_price,
                        expected_days=trade.expected_days,
                        last_price=fill,
                        last_mark_at=now,
                    )
                )
            venue = getattr(self.broker, "name", "broker")
            console.print(
                f"[green]Bought {trade.shares} {trade.symbol} @ {fill:.2f} via {venue}[/green]"
                f"  stop {trade.stop:.2f}  target {trade.take_profit:.2f}"
            )
        return state

    def monitor_exits(self, state: BotState) -> BotState:
        assert self.market is not None
        if not state.positions:
            return state
        remaining: list[Position] = []
        for pos in list(state.positions):
            try:
                quote = self.market.quote(pos.symbol)
                price = quote.price
            except Exception as exc:
                console.print(f"[yellow]Could not price {pos.symbol}: {exc}[/yellow]")
                remaining.append(pos)
                continue
            pos.peak_price = max(pos.peak_price or pos.entry_price, price)
            news_kill = None
            try:
                news = self.market.finnhub.company_news(pos.symbol, days=3)
                news_kill = self.market.severe_negative_news(news)
            except Exception:
                news_kill = None
            reason = self.risk.exit_reason(pos, price, news_kill)
            if not reason:
                remaining.append(pos)
                continue
            allowed, block = self.risk.can_sell(state, pos, reason)
            if not allowed:
                console.print(f"[yellow]Hold {pos.symbol}: {block}[/yellow]")
                remaining.append(pos)
                continue
            if not self.settings.execute:
                console.print(
                    f"[cyan]Would SELL {pos.shares} {pos.symbol} @ {price:.2f} "
                    f"reason={reason} (pass --execute to send the order)[/cyan]"
                )
                remaining.append(pos)
                continue
            if self.broker is None:
                self.connect_broker()
            sell = self.broker.sell_market(pos.symbol, pos.shares)
            if not sell.ok:
                console.print(f"[red]SELL failed {pos.symbol}: {sell.message}[/red]")
                remaining.append(pos)
                continue
            if pos.stop_order_id:
                self.broker.cancel(pos.stop_order_id)
            exit_px = sell.filled_price or price
            pnl = self.risk.close_position(state, pos, exit_px, reason=reason or "exit")
            color = "green" if pnl >= 0 else "red"
            console.print(
                f"[{color}]SELL {pos.shares} {pos.symbol} @ {exit_px:.2f} "
                f"reason={reason} pnl={pnl:+.2f}[/{color}]"
            )
            console.print(f"  why we sold: {pos.why_sell}")
        state.positions = remaining
        return state


def print_disclaimer() -> None:
    console.print(
        Panel(
            "[bold]No bot can guarantee profit or zero loss.[/bold]\n"
            "Stops can gap. AI can be wrong. You only risk the cash you allocate.",
            title="Risk",
            border_style="yellow",
        )
    )


def print_candidates(ranked: list[tuple[Snapshot, AiDecision]], proposals: list[ProposedTrade]) -> None:
    table = Table(title="AI stock suggestions (US)")
    table.add_column("Ticker")
    table.add_column("Action")
    table.add_column("Conf")
    table.add_column("Risk")
    table.add_column("Price")
    table.add_column("Shares")
    table.add_column("Stop")
    table.add_column("Target")
    table.add_column("Veto")
    by_symbol = {p.symbol: p for p in proposals}
    for snap, decision in ranked:
        trade = by_symbol.get(snap.symbol)
        table.add_row(
            snap.symbol,
            decision.action,
            f"{decision.confidence:.2f}",
            str(decision.risk_score),
            f"${snap.quote.price:.2f}",
            str(trade.shares if trade else ""),
            f"${trade.stop:.2f}" if trade else "",
            f"${trade.take_profit:.2f}" if trade else "",
            (trade.veto_reason[:40] if trade and trade.vetoed else ""),
        )
    console.print(table)
    for snap, decision in ranked:
        trade = by_symbol.get(snap.symbol)
        console.print(f"\n[bold]{snap.symbol}[/bold]  {decision.action}  horizon {decision.horizon_days}d")
        console.print(f"  [green]Why buy:[/green] {decision.why_buy}")
        console.print(f"  [red]Why sell:[/red] {decision.why_sell}")
        console.print(f"  [yellow]Risk:[/yellow] {decision.risk_notes}")
        if trade and trade.vetoed:
            console.print(f"  [magenta]Risk engine veto:[/magenta] {trade.veto_reason}")
        if snap.screen_notes:
            console.print(f"  Screen: {'; '.join(snap.screen_notes[:4])}")


def print_status(state: BotState, settings: Settings, advisor_name: str) -> None:
    console.print(
        Panel(
            f"Allocated capital: [bold]${state.allocated_capital:,.2f}[/bold]\n"
            f"Mode: {state.mode}   Horizon: {settings.horizon_days}d\n"
            f"Cash in bot book: ${state.cash():,.2f}   Invested (cost): ${state.invested():,.2f}\n"
            f"Realized P&L: ${state.realized_pnl:,.2f}\n"
            f"Open positions: {len(state.positions)} / {settings.risk.max_positions}\n"
            f"Kill switch: {state.kill_switch} {state.kill_reason}\n"
            f"AI provider: {advisor_name}",
            title="Bot book",
        )
    )
    if not state.positions:
        return
    table = Table(title="Open positions (allocated book only)")
    table.add_column("Symbol")
    table.add_column("Shares")
    table.add_column("Entry")
    table.add_column("Stop")
    table.add_column("Target")
    table.add_column("Age")
    table.add_column("Why buy")
    for pos in state.positions:
        table.add_row(
            pos.symbol,
            str(pos.shares),
            f"{pos.entry_price:.2f}",
            f"{pos.stop_price:.2f}",
            f"{pos.take_profit:.2f}",
            f"{pos.age_days()}d",
            pos.why_buy[:48],
        )
    console.print(table)


def run_cycle(settings: Settings, *, capital_override: bool, suggest_only: bool) -> BotState:
    print_disclaimer()
    state = load_state()
    state = apply_capital(state, settings, capital_override)
    bot = TradingBot(settings)
    bot.connect_data()
    state = bot.risk.reset_daily_if_needed(state, state.allocated_capital + state.realized_pnl)

    prices = {}
    for pos in state.positions:
        try:
            prices[pos.symbol] = bot.market.quote(pos.symbol).price
        except Exception:
            prices[pos.symbol] = pos.entry_price
    equity = bot.risk.mark_to_market(state, prices)
    state = bot.risk.apply_kill_switch(state, equity)

    print_status(state, settings, bot.advisor.provider)

    if not suggest_only:
        if settings.execute:
            bot.connect_broker()
        state = bot.monitor_exits(state)

    console.print("\n[bold]Scanning universe…[/bold] (Finnhub; may take a minute)")
    snaps = bot.scan()
    ranked = bot.advise(snaps)
    proposals = bot.propose(state, ranked)
    print_candidates(ranked, proposals)

    if suggest_only:
        save_state(state)
        console.print("\n[dim]Suggest-only. No orders sent. Use `python -m bot run --execute` for sandbox.[/dim]")
        return state

    if settings.live:
        console.print(
            Panel(
                "[bold red]LIVE Webull mode. Real money. Type is not requested again.[/bold red]\n"
                "Kill switch, stops, and allocated-capital cap still apply.",
                title="LIVE",
                border_style="red",
            )
        )
    elif settings.execute:
        console.print("[cyan]Sandbox / paper execution on.[/cyan]")

    bot.connect_broker()
    if settings.execute:
        state = bot.execute_buys(state, proposals)
        state = bot.monitor_exits(state)
    else:
        console.print("\n[dim]Dry run: pass --execute to send sandbox orders.[/dim]")

    save_state(state)
    print_status(state, settings, bot.advisor.provider)
    return state


def watch_loop(settings: Settings, capital_override: bool) -> None:
    import time

    run_cycle(settings, capital_override=capital_override, suggest_only=False)
    while True:
        console.print(f"\nSleeping {settings.watch_seconds}s. Ctrl+C to stop.")
        time.sleep(settings.watch_seconds)
        run_cycle(settings, capital_override=False, suggest_only=False)
