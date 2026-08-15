from __future__ import annotations

import argparse
import sys

from rich.console import Console

from bot.config import load_settings
from bot.models import Mode
from bot.runner import print_disclaimer, print_status, run_cycle, watch_loop
from bot.state import load_state, save_state

console = Console()


def _add_shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--capital",
        type=float,
        default=None,
        help="Cash this bot may use. Default 1000 on first run; later uses the saved amount unless you pass this flag.",
    )
    parser.add_argument(
        "--mode",
        choices=("short", "swing"),
        default=None,
        help="short = ~10 days, smaller names. swing = ~1 month, stronger names.",
    )
    parser.add_argument("--top", type=int, default=5, help="How many names AI should explain.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bot",
        description="US AI trading bot (Webull). You set the budget; the bot never auto-increases it.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    suggest = sub.add_parser("suggest", help="Scan + AI why-buy/why-sell. No orders.")
    _add_shared(suggest)

    run = sub.add_parser("run", help="Scan, size trades, check exits. Optional sandbox/live orders.")
    _add_shared(run)
    run.add_argument(
        "--execute",
        action="store_true",
        help="Place orders. Uses Webull sandbox unless --live is also set.",
    )
    run.add_argument(
        "--live",
        action="store_true",
        help="Use Webull production API (real money). Requires --execute.",
    )
    run.add_argument(
        "--watch",
        action="store_true",
        help="Keep looping: rescan and manage exits.",
    )
    run.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between watch loops (default 300).",
    )

    cap = sub.add_parser("set-capital", help="Save a new allocated budget (does not touch leftover Webull cash).")
    cap.add_argument("amount", type=float, help="New allocated capital, e.g. 2500")

    sub.add_parser("status", help="Show allocated capital, positions, kill switch.")
    sub.add_parser("ui", help="Open the Streamlit control panel.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "set-capital":
        if args.amount <= 0:
            console.print("[red]Capital must be positive.[/red]")
            return 2
        state = load_state()
        state.allocated_capital = float(args.amount)
        save_state(state)
        console.print(f"Allocated capital is now ${state.allocated_capital:,.2f}. Extra Webull cash is untouched.")
        return 0

    if args.cmd == "status":
        print_disclaimer()
        state = load_state()
        settings = load_settings(capital=state.allocated_capital, mode=state.mode)
        print_status(state, settings, "n/a")
        return 0

    if args.cmd == "ui":
        import subprocess

        from bot.config import ROOT

        app = ROOT / "app.py"
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)])

    capital_override = args.capital is not None
    mode: Mode = args.mode or load_state().mode or "short"
    capital = args.capital if args.capital is not None else load_state().allocated_capital or 1000.0
    if capital <= 0:
        console.print("[red]Capital must be positive.[/red]")
        return 2

    execute = bool(getattr(args, "execute", False))
    live = bool(getattr(args, "live", False))
    watch = bool(getattr(args, "watch", False))
    if live and not execute:
        console.print("[red]--live requires --execute.[/red]")
        return 2

    settings = load_settings(
        capital=capital,
        mode=mode,
        execute=execute,
        live=live,
        watch=watch,
        watch_seconds=int(getattr(args, "interval", 300)),
        top_n=int(args.top),
    )

    try:
        if args.cmd == "suggest":
            run_cycle(settings, capital_override=capital_override, suggest_only=True)
            return 0
        if watch:
            watch_loop(settings, capital_override=capital_override)
            return 0
        run_cycle(settings, capital_override=capital_override, suggest_only=False)
        return 0
    except KeyboardInterrupt:
        console.print("\nStopped.")
        return 130
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
