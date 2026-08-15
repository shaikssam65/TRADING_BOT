from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bot.models import Position


@dataclass
class OrderResult:
    ok: bool
    client_order_id: str = ""
    broker_order_id: str = ""
    message: str = ""
    filled_price: float = 0.0
    filled_qty: int = 0


class Broker(Protocol):
    name: str

    def buy_limit(self, symbol: str, shares: int, limit_price: float) -> OrderResult: ...

    def sell_market(self, symbol: str, shares: int) -> OrderResult: ...

    def place_stop_sell(self, symbol: str, shares: int, stop_price: float) -> OrderResult: ...

    def cancel(self, order_id: str) -> OrderResult: ...

    def positions(self) -> list[Position]: ...
