from __future__ import annotations

import uuid
from typing import Any

from bot.broker.base import OrderResult
from bot.models import Position


class WebullBroker:
    """Official Webull OpenAPI wrapper. Live is opt-in via settings.live."""

    def __init__(self, app_key: str, app_secret: str, account_id: str = "", live: bool = False) -> None:
        if not app_key or not app_secret:
            raise RuntimeError("WEBULL_APP_KEY and WEBULL_APP_SECRET are required for execution.")
        try:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import TradeClient
        except ImportError as exc:
            raise RuntimeError(
                "Install the official SDK: pip install webull-openapi-python-sdk"
            ) from exc

        self.live = live
        self.name = "webull-live" if live else "webull-sandbox"
        host = "api.webull.com" if live else "api.sandbox.webull.com"
        api_client = ApiClient(app_key, app_secret, "us")
        api_client.add_endpoint("us", host)
        self._trade = TradeClient(api_client)
        self.account_id = account_id or self._lookup_account()

    def _lookup_account(self) -> str:
        res = self._trade.account_v2.get_account_list()
        payload = _as_json(res)
        rows = payload.get("data") or payload.get("account_list") or payload
        if isinstance(rows, dict):
            rows = rows.get("list") or [rows]
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(f"No Webull accounts returned: {payload}")
        first = rows[0]
        if isinstance(first, dict):
            return str(first.get("account_id") or first.get("accountId") or first.get("id") or "")
        raise RuntimeError(f"Could not parse account id from {payload}")

    def buy_limit(self, symbol: str, shares: int, limit_price: float) -> OrderResult:
        return self._place(
            symbol=symbol,
            side="BUY",
            order_type="LIMIT",
            quantity=shares,
            limit_price=limit_price,
        )

    def sell_market(self, symbol: str, shares: int) -> OrderResult:
        return self._place(
            symbol=symbol,
            side="SELL",
            order_type="MARKET",
            quantity=shares,
        )

    def place_stop_sell(self, symbol: str, shares: int, stop_price: float) -> OrderResult:
        return self._place(
            symbol=symbol,
            side="SELL",
            order_type="STOP",
            quantity=shares,
            stop_price=stop_price,
        )

    def cancel(self, order_id: str) -> OrderResult:
        try:
            res = self._trade.order_v3.cancel_order(self.account_id, [order_id])
            payload = _as_json(res)
            ok = getattr(res, "status_code", 200) == 200
            return OrderResult(
                ok=ok,
                broker_order_id=order_id,
                client_order_id=order_id,
                message=str(payload),
            )
        except Exception as exc:
            return OrderResult(ok=False, broker_order_id=order_id, message=str(exc))

    def positions(self) -> list[Position]:
        try:
            getter = getattr(self._trade, "account_v2", None)
            if getter is None:
                return []
            fn = getattr(getter, "get_account_position", None) or getattr(
                getter, "get_positions", None
            )
            if fn is None:
                return []
            res = fn(self.account_id)
            payload = _as_json(res)
        except Exception:
            return []
        rows = payload.get("data") or payload.get("positions") or payload.get("list") or []
        if isinstance(rows, dict):
            rows = rows.get("list") or rows.get("positions") or []
        out: list[Position] = []
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or row.get("ticker") or "")
            qty = int(float(row.get("quantity") or row.get("qty") or 0))
            px = float(row.get("average_price") or row.get("avgPrice") or row.get("costPrice") or 0)
            if symbol and qty > 0:
                out.append(
                    Position(
                        symbol=symbol,
                        mode="short",
                        shares=qty,
                        entry_price=px,
                        stop_price=0.0,
                        take_profit=0.0,
                        opened_at="",
                        horizon_days=10,
                    )
                )
        return out

    def _place(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: int,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> OrderResult:
        client_order_id = uuid.uuid4().hex
        order: dict[str, Any] = {
            "combo_type": "NORMAL",
            "client_order_id": client_order_id,
            "symbol": symbol.upper(),
            "instrument_type": "EQUITY",
            "market": "US",
            "order_type": order_type,
            "quantity": str(quantity),
            "support_trading_session": "CORE",
            "side": side,
            "time_in_force": "DAY",
            "entrust_type": "QTY",
        }
        if limit_price is not None:
            order["limit_price"] = f"{limit_price:.4f}".rstrip("0").rstrip(".")
        if stop_price is not None:
            order["stop_price"] = f"{stop_price:.4f}".rstrip("0").rstrip(".")
            # Some Webull stop orders also want a limit a tick below.
            if order_type == "STOP" and "limit_price" not in order:
                order["order_type"] = "STOP_LOSS"
                order["limit_price"] = f"{stop_price:.4f}".rstrip("0").rstrip(".")
        try:
            res = self._trade.order_v3.place_order(self.account_id, [order])
            payload = _as_json(res)
            ok = getattr(res, "status_code", 200) == 200
            broker_id = _extract_order_id(payload) or client_order_id
            return OrderResult(
                ok=ok,
                client_order_id=client_order_id,
                broker_order_id=str(broker_id),
                message=str(payload)[:500],
                filled_price=float(limit_price or stop_price or 0),
                filled_qty=quantity if ok else 0,
            )
        except Exception as exc:
            return OrderResult(ok=False, client_order_id=client_order_id, message=str(exc))


def _as_json(res: Any) -> dict:
    if res is None:
        return {}
    if isinstance(res, dict):
        return res
    if hasattr(res, "json"):
        try:
            data = res.json()
            return data if isinstance(data, dict) else {"data": data}
        except Exception:
            return {"raw": str(res)}
    return {"raw": str(res)}


def _extract_order_id(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("order_id", "orderId", "client_order_id"):
        if payload.get(key):
            return str(payload[key])
    data = payload.get("data")
    if isinstance(data, dict):
        return _extract_order_id(data)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _extract_order_id(data[0])
    return ""
