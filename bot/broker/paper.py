from __future__ import annotations

import uuid
from datetime import datetime, timezone

from bot.broker.base import OrderResult
from bot.models import Position


class PaperBroker:
    """Local fill simulator used when Webull credentials are missing or execute is off."""

    name = "paper-local"

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, dict] = {}

    def buy_limit(self, symbol: str, shares: int, limit_price: float) -> OrderResult:
        oid = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = self._positions.get(symbol)
        if existing:
            total = existing.shares + shares
            existing.entry_price = (
                existing.entry_price * existing.shares + limit_price * shares
            ) / total
            existing.shares = total
            existing.client_order_id = oid
        else:
            self._positions[symbol] = Position(
                symbol=symbol,
                mode="short",
                shares=shares,
                entry_price=limit_price,
                stop_price=0.0,
                take_profit=0.0,
                opened_at=now,
                horizon_days=10,
                client_order_id=oid,
            )
        return OrderResult(
            ok=True,
            client_order_id=oid,
            broker_order_id=oid,
            message="Paper fill (local simulator)",
            filled_price=limit_price,
            filled_qty=shares,
        )

    def sell_market(self, symbol: str, shares: int) -> OrderResult:
        oid = uuid.uuid4().hex
        self._positions.pop(symbol, None)
        return OrderResult(
            ok=True,
            client_order_id=oid,
            broker_order_id=oid,
            message="Paper sell (local simulator)",
            filled_price=0.0,
            filled_qty=shares,
        )

    def place_stop_sell(self, symbol: str, shares: int, stop_price: float) -> OrderResult:
        oid = uuid.uuid4().hex
        self._orders[oid] = {
            "symbol": symbol,
            "shares": shares,
            "stop": stop_price,
            "type": "STOP",
        }
        if symbol in self._positions:
            self._positions[symbol].stop_price = stop_price
            self._positions[symbol].stop_order_id = oid
        return OrderResult(
            ok=True,
            client_order_id=oid,
            broker_order_id=oid,
            message="Paper stop recorded locally",
        )

    def cancel(self, order_id: str) -> OrderResult:
        self._orders.pop(order_id, None)
        return OrderResult(ok=True, client_order_id=order_id, broker_order_id=order_id, message="Cancelled")

    def positions(self) -> list[Position]:
        return list(self._positions.values())
