from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from bot.config import DATA_DIR, STATE_PATH
from bot.models import BotState, ClosedTrade, DayTrade, PendingApproval, Position

T = TypeVar("T")


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _from_dict(cls: type[T], data: dict[str, Any]) -> T:
    allowed = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in allowed})  # type: ignore[arg-type]


def load_state() -> BotState:
    _ensure_dir()
    if not STATE_PATH.exists():
        return BotState()
    raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    positions = [_from_dict(Position, p) for p in raw.get("positions", [])]
    day_trades = [_from_dict(DayTrade, d) for d in raw.get("day_trades", [])]
    pending = [_from_dict(PendingApproval, p) for p in raw.get("pending", [])]
    closed_trades = [_from_dict(ClosedTrade, t) for t in raw.get("closed_trades", [])]
    return BotState(
        allocated_capital=float(raw.get("allocated_capital", 1000.0)),
        mode=raw.get("mode", "short"),
        positions=positions,
        day_trades=day_trades,
        pending=pending,
        closed_trades=closed_trades,
        last_advice=list(raw.get("last_advice") or []),
        daily_start_equity=float(raw.get("daily_start_equity", 1000.0)),
        daily_start_date=raw.get("daily_start_date", ""),
        kill_switch=bool(raw.get("kill_switch", False)),
        kill_reason=raw.get("kill_reason", ""),
        realized_pnl=float(raw.get("realized_pnl", 0.0)),
        updated_at=raw.get("updated_at", ""),
    )


def save_state(state: BotState) -> Path:
    _ensure_dir()
    state.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = asdict(state)
    STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return STATE_PATH
