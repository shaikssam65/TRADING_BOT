from __future__ import annotations

from bot.models import Bar, Quote


class WebullMarketData:
    """Thin wrapper around the official Webull market-data SDK.

    Quotes for screening still come from Finnhub when this client is unavailable
    (no SDK install or no credentials). Live execution should still use Webull
    prices when the SDK is present.
    """

    def __init__(self, app_key: str, app_secret: str, live: bool = False) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.live = live
        self._client = None
        self.available = False
        if app_key and app_secret:
            self._client = self._try_connect()
            self.available = self._client is not None

    def _try_connect(self):
        try:
            from webull.core.client import ApiClient
            from webull.data.data_client import DataClient
        except ImportError:
            return None
        try:
            api_client = ApiClient(self.app_key, self.app_secret, "us")
            host = "api.webull.com" if self.live else "api.sandbox.webull.com"
            api_client.add_endpoint("us", host)
            return DataClient(api_client)
        except Exception:
            return None

    def quote(self, symbol: str) -> Quote | None:
        if not self._client:
            return None
        getters = (
            "get_snapshot",
            "snapshot",
            "get_quotes",
            "get_quote",
        )
        data = None
        for name in getters:
            fn = getattr(self._client, name, None)
            if fn is None:
                continue
            try:
                data = fn(symbol)
                break
            except TypeError:
                try:
                    data = fn(symbols=symbol)
                    break
                except Exception:
                    continue
            except Exception:
                continue
        parsed = _parse_webull_quote(symbol, data)
        return parsed

    def candles(self, symbol: str, days: int = 60) -> list[Bar]:
        if not self._client:
            return []
        fn = getattr(self._client, "get_history_bar", None) or getattr(
            self._client, "history_bar", None
        )
        if fn is None:
            return []
        try:
            raw = fn(symbol, "d1", days)
        except Exception:
            return []
        return _parse_webull_bars(raw)


def _parse_webull_quote(symbol: str, data) -> Quote | None:
    if data is None:
        return None
    payload = data
    if hasattr(data, "json"):
        try:
            payload = data.json()
        except Exception:
            payload = data
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict) and "data" in payload:
        inner = payload["data"]
        if isinstance(inner, list) and inner:
            payload = inner[0]
        elif isinstance(inner, dict):
            payload = inner
    if not isinstance(payload, dict):
        return None
    price = _first_float(payload, "close", "last", "lastPrice", "tradePrice", "c", "price")
    if not price:
        return None
    prev = _first_float(payload, "preClose", "prevClose", "previousClose", "pc") or price
    change_pct = 0.0
    if prev:
        change_pct = (price - prev) / prev * 100
    return Quote(
        symbol=symbol,
        price=price,
        change_pct=change_pct,
        high=_first_float(payload, "high", "h") or price,
        low=_first_float(payload, "low", "l") or price,
        open=_first_float(payload, "open", "o") or price,
        prev_close=prev,
    )


def _parse_webull_bars(raw) -> list[Bar]:
    payload = raw
    if hasattr(raw, "json"):
        try:
            payload = raw.json()
        except Exception:
            payload = raw
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("bars") or payload.get("list") or []
    bars: list[Bar] = []
    if not isinstance(rows, list):
        return bars
    for row in rows:
        if not isinstance(row, dict):
            continue
        close = _first_float(row, "close", "c")
        if not close:
            continue
        bars.append(
            Bar(
                time=int(_first_float(row, "time", "t") or 0),
                open=_first_float(row, "open", "o") or close,
                high=_first_float(row, "high", "h") or close,
                low=_first_float(row, "low", "l") or close,
                close=close,
                volume=_first_float(row, "volume", "v") or 0,
            )
        )
    return bars


def _first_float(data: dict, *keys: str) -> float | None:
    for key in keys:
        value = data.get(key)
        try:
            if value is not None and value != "":
                return float(value)
        except (TypeError, ValueError):
            continue
    return None
