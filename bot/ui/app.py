from __future__ import annotations

import streamlit as st

from bot.config import RiskLimits, get_secret, load_settings, load_ui_settings, save_ui_settings
from bot.service import (
    execute_approved,
    monitor_positions,
    queue_adds,
    queue_sells,
    refresh_marks,
    set_pending_status,
    suggest_and_queue,
)
from bot.state import load_state, save_state
from bot.strategies.universe import FILTER_LABELS
from bot.textfmt import to_bullets

MODEL_CHOICES = ["gpt-5.6", "gpt-5.4", "gpt-5", "gpt-4.1", "gpt-4o"]
FILTER_KEYS = list(FILTER_LABELS.keys())
ACTION_LABEL = {
    "BUY": "Buy",
    "ADD": "Buy more",
    "HOLD": "Watch / hold",
    "SELL": "Sell",
    "AVOID": "Skip",
}


def _show_bullets(*parts: object) -> None:
    lines = to_bullets(*parts, limit=4)
    if not lines:
        return
    st.markdown("\n".join(f"- {line}" for line in lines))


def _idea_pnl_caption(item) -> None:
    """Dollar P&L if the scenario target or stop hits."""
    if not item.shares:
        return
    bits = []
    if item.expected_price:
        win = (item.expected_price - item.entry) * item.shares
        bits.append(
            f"if target hits: **${win:+,.2f}** "
            f"({item.expected_return_pct:+.1f}% in ~{item.expected_days}d)"
        )
    if item.stop:
        loss = (item.stop - item.entry) * item.shares
        bits.append(f"if stop hits: **${loss:+,.2f}**")
    if bits:
        st.caption("Scenario P&L " + " · ".join(bits))


def _book_banner() -> None:
    state = load_state()
    unreal = sum(p.last_pnl for p in state.positions)
    st.sidebar.markdown("**Saved book** (`data/bot_state.json`)")
    st.sidebar.metric("Open names", str(len(state.positions)))
    st.sidebar.metric("Unrealized P&L", f"${unreal:+,.2f}")
    st.sidebar.metric("Realized P&L", f"${state.realized_pnl:+,.2f}")
    pending_n = len([p for p in state.pending if p.status in {"pending", "approved"}])
    st.sidebar.caption(f"{pending_n} ideas waiting · loads again tomorrow")


def _persist_sidebar(capital, mode, filters, top_n, model, sandbox, live) -> None:
    save_ui_settings(
        {
            "capital": float(capital),
            "mode": mode,
            "price_filters": list(filters),
            "top_n": int(top_n),
            "openai_model": model,
            "execute_sandbox": bool(sandbox),
            "live": bool(live),
        }
    )


def _settings_from_ui(ui: dict, *, execute: bool = False):
    risk = RiskLimits(
        max_positions=int(ui.get("max_positions", 3)),
        max_position_pct=float(ui.get("max_position_pct", 0.25)),
        cash_buffer_pct=float(ui.get("cash_buffer_pct", 0.20)),
        short_stop_pct=float(ui.get("short_stop_pct", 0.05)),
        swing_stop_pct=float(ui.get("swing_stop_pct", 0.07)),
        short_take_profit_pct=float(ui.get("short_tp", 0.06)),
        swing_take_profit_pct=float(ui.get("swing_tp", 0.10)),
    )
    return load_settings(
        capital=float(ui["capital"]),
        mode=ui["mode"],
        execute=execute,
        live=bool(ui.get("live")),
        top_n=int(ui["top_n"]),
        openai_model=ui["openai_model"],
        price_filters=list(ui.get("price_filters") or []),
        require_approval=True,
        risk=risk,
    )


def _sidebar() -> dict:
    saved = load_ui_settings()
    st.sidebar.title("Trading bot")
    st.sidebar.caption("You set the cash. The bot never raises it.")
    capital = st.sidebar.number_input(
        "Capital the bot may use ($)",
        min_value=100.0,
        max_value=1_000_000.0,
        value=float(saved.get("capital", 1000)),
        step=100.0,
    )
    if st.sidebar.button("Save capital"):
        state = load_state()
        state.allocated_capital = float(capital)
        save_state(state)
        st.sidebar.success(f"Allocated ${capital:,.0f}")

    mode = st.sidebar.radio(
        "Horizon",
        options=["short", "swing"],
        index=0 if saved.get("mode", "short") == "short" else 1,
        format_func=lambda m: "~10 days (short)" if m == "short" else "~1 month (swing)",
    )
    default_bucket = (saved.get("price_filters") or ["top20_overall"])[0]
    if default_bucket not in FILTER_KEYS:
        default_bucket = "top20_overall"
    bucket = st.sidebar.radio(
        "Suggestion type",
        options=FILTER_KEYS,
        index=FILTER_KEYS.index(default_bucket),
        format_func=lambda k: FILTER_LABELS[k],
    )
    filters = [bucket]
    model = st.sidebar.selectbox(
        "ChatGPT model",
        MODEL_CHOICES,
        index=MODEL_CHOICES.index(saved["openai_model"])
        if saved.get("openai_model") in MODEL_CHOICES
        else 0,
    )
    sandbox = st.sidebar.checkbox(
        "Use Webull sandbox when I approve",
        value=bool(saved.get("execute_sandbox", True)),
    )
    live = st.sidebar.checkbox(
        "Live money (dangerous)",
        value=bool(saved.get("live", False)),
    )
    st.sidebar.divider()
    st.sidebar.markdown("**Risk (customizable)**")
    max_positions = st.sidebar.slider("Max open names", 1, 5, int(saved.get("max_positions", 3)))
    max_position_pct = st.sidebar.slider(
        "Max % per name", 0.10, 0.40, float(saved.get("max_position_pct", 0.25))
    )
    cash_buffer_pct = st.sidebar.slider(
        "Cash buffer %", 0.05, 0.40, float(saved.get("cash_buffer_pct", 0.20))
    )
    ui = {
        "capital": capital,
        "mode": mode,
        "price_filters": filters,
        "top_n": 10 if bucket != "top20_overall" else 20,
        "openai_model": model,
        "execute_sandbox": sandbox,
        "live": live,
        "max_positions": max_positions,
        "max_position_pct": max_position_pct,
        "cash_buffer_pct": cash_buffer_pct,
        "short_stop_pct": float(saved.get("short_stop_pct", 0.05)),
        "swing_stop_pct": float(saved.get("swing_stop_pct", 0.07)),
        "short_tp": float(saved.get("short_tp", 0.06)),
        "swing_tp": float(saved.get("swing_tp", 0.10)),
    }
    _persist_sidebar(
        capital,
        mode,
        filters,
        10 if bucket != "top20_overall" else 20,
        model,
        sandbox,
        live,
    )
    save_ui_settings({**saved, **ui})
    return ui


def _page_suggest(ui: dict) -> None:
    st.header("Suggest buys")
    st.write(
        "Pick one suggestion type, then get news-backed ideas. "
        "**Nothing is bought in Webull until you connect the API and approve it.** "
        "Without Webull keys, Approve still records a **paper** (practice) fill on this PC. "
        "Queued ideas stay in `data/bot_state.json` overnight."
    )
    settings = _settings_from_ui(ui)
    if not settings.finnhub_api_key:
        st.error("Add FINNHUB_API_KEY to `.env` (free at finnhub.io) so the bot can read prices and news.")
        return
    if not settings.openai_api_key:
        st.warning("No OPENAI_API_KEY in `.env`. Suggestions will use the built-in heuristic until you add a key.")
    if not ui.get("price_filters"):
        st.info("Select at least one filter in the sidebar.")
        return

    if st.button("Get suggestions", type="primary"):
        with st.spinner("Scanning news and prices… this can take a minute"):
            try:
                state, proposals, provider = suggest_and_queue(settings)
            except Exception as exc:
                st.exception(exc)
                return
        st.session_state["last_proposals"] = True
        st.success(f"Queued {len(proposals)} ideas for your review. AI: {provider}")

    state = load_state()
    buys = [p for p in state.pending if p.kind == "buy" and p.status == "pending"]
    if not buys:
        st.caption("No pending buy ideas yet.")
        return

    st.subheader("Ideas waiting for you")
    for item in buys:
        with st.container(border=True):
            title = f"{item.symbol}  {item.name}".strip()
            st.markdown(f"### {title}")
            cols = st.columns(5)
            cols[0].metric("Price now", f"${item.entry:.2f}")
            cols[1].metric("Days it may work", f"{item.expected_days}d")
            cols[2].metric("Scenario price", f"${item.expected_price:.2f}" if item.expected_price else "—")
            cols[3].metric("Scenario move", f"{item.expected_return_pct:+.1f}%")
            cols[4].metric("Suggested shares", str(item.shares) if not item.vetoed else "0")
            _idea_pnl_caption(item)
            st.markdown(f"**Why this name (3–4 points):**")
            _show_bullets(item.bullets, item.why_buy, item.why_sell, item.reasoning)
            st.caption(item.risk_notes)
            if item.vetoed:
                st.warning(f"Risk engine blocked a live order: {item.veto_reason}")
            else:
                st.caption(
                    f"Stop ${item.stop:.2f} · take-profit ${item.take_profit:.2f} · "
                    f"confidence {item.confidence:.2f} · risk {item.risk_score}/10 · ${item.notional:.0f}"
                )


def _page_approve(ui: dict) -> None:
    st.header("Human approval")
    st.write(
        "Review each idea. Without a Webull API, execute fills the **local paper book only** "
        "(not your real Webull account). See [FLOW.md](FLOW.md)."
    )
    state = load_state()
    pending = [p for p in state.pending if p.status == "pending"]
    approved = [p for p in state.pending if p.status == "approved"]
    if not pending and not approved:
        st.info("Nothing waiting. Run **Suggest buys** or **Monitor** first.")
        return

    if pending:
        st.subheader("Waiting for your decision")
        selected = []
        for item in pending:
            kind = {"buy": "BUY", "add": "BUY MORE", "sell": "SELL"}.get(item.kind, item.kind.upper())
            label = (
                f"{kind} {item.symbol} · {item.shares} sh · "
                f"{item.expected_days}d · scenario {item.expected_return_pct:+.1f}%"
            )
            if item.urgent:
                label = "URGENT · " + label
            if st.checkbox(label, key=f"sel_{item.id}", value=not item.vetoed):
                selected.append(item.id)
            _idea_pnl_caption(item)
            _show_bullets(item.bullets, item.reasoning, item.why_buy)
            if item.vetoed:
                st.caption(f"Blocked: {item.veto_reason}")

        c1, c2 = st.columns(2)
        if c1.button("Approve selected"):
            if not selected:
                st.warning("Select at least one row.")
            else:
                set_pending_status(selected, "approved")
                st.rerun()
        if c2.button("Reject selected"):
            if selected:
                set_pending_status(selected, "rejected")
                st.rerun()

    if approved:
        st.subheader("Approved — waiting to send")
        for item in approved:
            st.write(
                f"- **{item.kind.upper()} {item.symbol}** {item.shares} sh @ ${item.entry:.2f} "
                f"(stop {item.stop:.2f})"
            )
        understood = st.checkbox("I understand this can lose money. Send the approved orders.")
        if ui.get("live"):
            st.error("Live Webull is on. This is real money.")
        elif not _settings_from_ui(ui).webull_app_key:
            st.info("No Webull API connected. Execute will only update the local paper book. Your Webull account will not change.")
        if st.button("Execute approved orders", type="primary", disabled=not understood):
            settings = _settings_from_ui(ui, execute=True)
            with st.spinner("Sending approved orders…"):
                try:
                    _, messages = execute_approved(settings)
                except Exception as exc:
                    st.exception(exc)
                    return
            for msg in messages:
                st.write(msg)
            st.rerun()


def _page_monitor(ui: dict) -> None:
    st.header("Monitor open positions")
    st.write(
        "Holdings are **saved to disk** and reloaded the next day. "
        "P&L is per stock. AI can say **Watch / hold**, **Buy more**, or **Sell**."
    )
    settings = _settings_from_ui(ui)
    try:
        state = refresh_marks(settings)
    except Exception:
        state = load_state()

    unreal = sum(p.last_pnl for p in state.positions)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open positions", str(len(state.positions)))
    c2.metric("Unrealized P&L", f"${unreal:+,.2f}")
    c3.metric("Realized P&L", f"${state.realized_pnl:+,.2f}")
    c4.metric("Total P&L", f"${unreal + state.realized_pnl:+,.2f}")

    if state.positions:
        st.subheader("Open book — profit and loss")
        rows = []
        for pos in state.positions:
            now_px = pos.last_price or pos.entry_price
            rows.append(
                {
                    "Symbol": pos.symbol,
                    "Shares": pos.shares,
                    "Entry": round(pos.entry_price, 2),
                    "Now": round(now_px, 2),
                    "P&L $": round(pos.last_pnl, 2),
                    "P&L %": round(pos.last_pnl_pct, 2),
                    "Age": f"{pos.age_days()}d",
                    "Stop": round(pos.stop_price, 2),
                    "Target": round(pos.take_profit, 2),
                }
            )
        st.dataframe(rows, hide_index=True, use_container_width=True)
    else:
        st.info("No open positions yet. Approve a BUY and execute — it will still be here tomorrow.")

    if state.closed_trades:
        st.subheader("Closed trades — realized P&L")
        closed = [
            {
                "Symbol": t.symbol,
                "Shares": t.shares,
                "Entry": round(t.entry_price, 2),
                "Exit": round(t.exit_price, 2),
                "P&L $": round(t.pnl, 2),
                "P&L %": round(t.pnl_pct, 2),
                "Reason": t.reason,
                "Closed": t.closed_at[:10],
            }
            for t in reversed(state.closed_trades[-20:])
        ]
        st.dataframe(closed, hide_index=True, use_container_width=True)

    if not state.positions:
        return

    if st.button("Refresh AI monitor", type="primary"):
        with st.spinner("Reviewing positions…"):
            try:
                state, advice, provider = monitor_positions(settings)
            except Exception as exc:
                st.exception(exc)
                return
        st.session_state["monitor_advice"] = [a.__dict__ for a in advice]
        st.caption(f"AI: {provider}")

    raw = st.session_state.get("monitor_advice") or state.last_advice
    if not raw:
        st.caption("Click **Refresh AI monitor** for hold / buy more / sell bullets.")
        return

    from bot.models import PositionAdvice

    advice = []
    for row in raw:
        try:
            advice.append(PositionAdvice(**{k: v for k, v in row.items() if k in PositionAdvice.__dataclass_fields__}))
        except Exception:
            continue
    sell_syms = []
    add_syms = []
    st.subheader("AI suggestions on what you hold")
    for adv in advice:
        with st.container(border=True):
            if adv.urgent:
                st.error(f"Urgent: {adv.engine_reason}")
            label = ACTION_LABEL.get(adv.action, adv.action)
            st.markdown(f"### {adv.symbol} · {label} · P&L ${adv.pnl:+,.2f} ({adv.pnl_pct:+.1f}%)")
            cols = st.columns(5)
            cols[0].metric("Now", f"${adv.price:.2f}")
            cols[1].metric("P&L", f"${adv.pnl:+.2f}", f"{adv.pnl_pct:+.1f}%")
            cols[2].metric("Days left (scenario)", f"{adv.expected_days}d")
            cols[3].metric("Scenario price", f"${adv.expected_price:.2f}" if adv.expected_price else "—")
            cols[4].metric("Confidence", f"{adv.confidence:.2f}")
            st.markdown("**Suggestion:**")
            _show_bullets(adv.bullets, adv.reasoning, adv.why_sell)
            if adv.headlines:
                st.caption("News: " + " · ".join(adv.headlines[:2]))
            if adv.action == "SELL":
                if st.checkbox(f"Queue {adv.symbol} sell for approval", key=f"sell_{adv.symbol}"):
                    sell_syms.append(adv.symbol)
            elif adv.action == "ADD":
                if st.checkbox(
                    f"Queue buy more {adv.symbol} ({adv.add_shares} sh) for approval",
                    key=f"add_{adv.symbol}",
                ):
                    add_syms.append(adv.symbol)

    c1, c2 = st.columns(2)
    if sell_syms and c1.button("Send selected sells to approval"):
        queue_sells(advice, sell_syms)
        st.success("Queued sells. Open **Approve** to confirm.")
        st.rerun()
    if add_syms and c2.button("Send selected buy-more to approval"):
        queue_adds(advice, add_syms)
        st.success("Queued buy-more. Open **Approve** to confirm.")
        st.rerun()


def _require_login() -> bool:
    password = get_secret("APP_PASSWORD")
    if not password:
        st.sidebar.warning("Set APP_PASSWORD in .env before you put this on the internet.")
        return True
    if st.session_state.get("authed"):
        if st.sidebar.button("Log out"):
            st.session_state.authed = False
            st.rerun()
        return True
    st.title("USA AI Trading Bot")
    st.caption("Sign in to continue.")
    entered = st.text_input("Password", type="password")
    if st.button("Sign in"):
        if entered == password:
            st.session_state.authed = True
            st.rerun()
        st.error("Wrong password.")
    return False


def main() -> None:
    st.set_page_config(page_title="USA AI Trading Bot", layout="wide")
    if not _require_login():
        return
    st.title("USA AI Trading Bot")
    st.caption("Suggestions from news and prices. Human approval before every order. Book is saved to disk and loaded next day.")
    ui = _sidebar()
    _book_banner()
    page = st.radio(
        "Step",
        ["Suggest buys", "Approve", "Monitor"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if page == "Suggest buys":
        _page_suggest(ui)
    elif page == "Approve":
        _page_approve(ui)
    else:
        _page_monitor(ui)


if __name__ == "__main__":
    main()
