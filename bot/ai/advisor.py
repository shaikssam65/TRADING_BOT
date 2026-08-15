from __future__ import annotations

import json
import re
from typing import Any

from bot.config import Settings
from bot.ml.predictor import Predictor, default_predictor
from bot.models import AiDecision, Position, Snapshot
from bot.textfmt import to_bullets

SYSTEM_PROMPT = """You are a cautious US equities research assistant for a retail trading bot.
You only use the structured facts provided. Do not invent prices, filings, or headlines.
You never promise profit or zero risk. Prefer AVOID when data is thin or mixed.
expected_days, expected_price, and expected_return_pct are a scenario estimate, not a guarantee.
Return ONLY valid JSON with this schema:
{
  "ticker": "XYZ",
  "action": "BUY" | "HOLD" | "AVOID" | "SELL",
  "confidence": 0.0,
  "horizon_days": 10,
  "expected_days": 10,
  "expected_price": 0.0,
  "expected_return_pct": 0.0,
  "bullets": ["short point 1", "short point 2", "short point 3"],
  "why_buy": "one short line",
  "why_sell": "one short line",
  "reasoning": "one short line",
  "risk_notes": "one short line",
  "risk_score": 1
}
bullets MUST be 3 or 4 short lines, never more. Each line under 20 words.
risk_score is 1 (safer) to 10 (very risky).
action BUY only if there is a clear, data-backed setup for the requested horizon.
If ml_prediction is present, you may use it as one input, never as the only reason.
"""

POSITION_PROMPT = """You are reviewing an OPEN US stock position for a retail bot.
Recommend HOLD (watch / keep), ADD (buy a little more), or SELL only. Use only the provided facts.
ADD only if the thesis is working and size is still small. HOLD means watch, do not add.
expected_* fields are a scenario if we keep holding, not a promise.
bullets MUST be 3 or 4 short lines, never more.
Return ONLY valid JSON with the same schema. action must be HOLD, ADD, or SELL.
"""

_FALLBACK_MODELS = ("gpt-5.6", "gpt-5.4", "gpt-5", "gpt-4.1", "gpt-4o")


class Advisor:
    def __init__(self, settings: Settings, predictor: Predictor | None = None) -> None:
        self.settings = settings
        self.predictor = predictor or default_predictor()
        self._openai = None
        self._anthropic = None
        self._openai_model = settings.openai_model
        if settings.openai_api_key:
            try:
                from openai import OpenAI

                self._openai = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self._openai = None
        elif settings.anthropic_api_key:
            try:
                import anthropic

                self._anthropic = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            except Exception:
                self._anthropic = None

    @property
    def provider(self) -> str:
        if self._openai:
            return f"openai:{self._openai_model}"
        if self._anthropic:
            return "anthropic"
        return "heuristic"

    def decide(self, snap: Snapshot, horizon_days: int, mode: str) -> AiDecision:
        self._attach_ml(snap)
        payload = _snapshot_payload(snap, horizon_days, mode)
        if self._openai or self._anthropic:
            try:
                return self._llm_decide(payload, snap.symbol, horizon_days, SYSTEM_PROMPT)
            except Exception:
                return heuristic_decision(snap, horizon_days, mode)
        return heuristic_decision(snap, horizon_days, mode)

    def review_position(
        self,
        pos: Position,
        snap: Snapshot,
        engine_reason: str | None,
        horizon_days: int,
        mode: str,
    ) -> AiDecision:
        self._attach_ml(snap)
        payload = _snapshot_payload(snap, horizon_days, mode)
        payload["open_position"] = {
            "shares": pos.shares,
            "entry_price": pos.entry_price,
            "stop_price": pos.stop_price,
            "take_profit": pos.take_profit,
            "age_days": pos.age_days(),
            "horizon_days": pos.horizon_days,
            "unrealized_pct": round((snap.quote.price / pos.entry_price - 1) * 100, 2)
            if pos.entry_price
            else 0,
            "engine_reason": engine_reason,
            "original_why_buy": pos.why_buy,
            "original_why_sell": pos.why_sell,
        }
        if self._openai or self._anthropic:
            try:
                decision = self._llm_decide(payload, pos.symbol, horizon_days, POSITION_PROMPT)
                if decision.action == "BUY":
                    decision.action = "ADD"
                if decision.action not in {"HOLD", "SELL", "ADD"}:
                    decision.action = "HOLD" if not engine_reason else "SELL"
                return decision
            except Exception:
                return heuristic_position(pos, snap, engine_reason, horizon_days)
        return heuristic_position(pos, snap, engine_reason, horizon_days)

    def _attach_ml(self, snap: Snapshot) -> None:
        try:
            pred = self.predictor.predict(snap)
        except Exception:
            pred = None
        if pred is None:
            return
        snap.ml_expected_return_pct = pred.expected_return_pct
        snap.ml_horizon_days = pred.horizon_days
        snap.ml_confidence = pred.confidence

    def _llm_decide(
        self, payload: dict[str, Any], ticker: str, horizon_days: int, system: str
    ) -> AiDecision:
        user = (
            f"Horizon {horizon_days} trading days. Facts:\n{json.dumps(payload, indent=2)}"
        )
        raw = ""
        if self._openai:
            raw = self._openai_complete(system, user)
        elif self._anthropic:
            resp = self._anthropic.messages.create(
                model=self.settings.anthropic_model,
                max_tokens=900,
                temperature=0.2,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            raw = "".join(
                block.text for block in resp.content if getattr(block, "type", "") == "text"
            )
        data = _parse_json(raw)
        return _decision_from_dict(data, ticker, horizon_days, payload.get("price") or 0)

    def _openai_complete(self, system: str, user: str) -> str:
        models = [self._openai_model, *[m for m in _FALLBACK_MODELS if m != self._openai_model]]
        last_err: Exception | None = None
        for model in models:
            kwargs_list = [
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                },
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                },
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            ]
            for kwargs in kwargs_list:
                try:
                    resp = self._openai.chat.completions.create(**kwargs)
                    self._openai_model = model
                    return resp.choices[0].message.content or "{}"
                except Exception as exc:
                    last_err = exc
                    msg = str(exc).lower()
                    if "temperature" in msg or "response_format" in msg:
                        continue
                    if "model" in msg or "not found" in msg or "does not exist" in msg:
                        break
                    continue
        raise RuntimeError(last_err or "OpenAI completion failed")


def _snapshot_payload(snap: Snapshot, horizon_days: int, mode: str) -> dict[str, Any]:
    headlines = [
        {"source": n.source, "headline": n.headline, "summary": n.summary[:240]}
        for n in snap.news[:6]
        if n.headline
    ]
    profile = snap.profile
    metrics = snap.metrics
    sent = snap.sentiment
    payload: dict[str, Any] = {
        "mode": mode,
        "horizon_days": horizon_days,
        "symbol": snap.symbol,
        "name": profile.name if profile else snap.symbol,
        "industry": profile.industry if profile else "",
        "market_cap": profile.market_cap if profile else 0,
        "price": snap.quote.price,
        "day_change_pct": snap.quote.change_pct,
        "rs_10d_pct": round(snap.rs_10d, 2),
        "rs_20d_pct": round(snap.rs_20d, 2),
        "above_sma20": snap.above_sma20,
        "above_sma50": snap.above_sma50,
        "avg_volume": snap.avg_volume,
        "volume_ratio": round(snap.volume_ratio, 2),
        "earnings_in_days": snap.earnings_in_days,
        "news_sentiment": sent.news_score if sent else None,
        "social_sentiment": sent.social_score if sent else None,
        "pe": metrics.pe if metrics else None,
        "revenue_growth": metrics.revenue_growth if metrics else None,
        "debt_to_equity": metrics.debt_to_equity if metrics else None,
        "roe": metrics.roe if metrics else None,
        "week_52_high": metrics.week_52_high if metrics else None,
        "week_52_low": metrics.week_52_low if metrics else None,
        "headlines": headlines,
        "screen_score": snap.screen_score,
        "screen_notes": snap.screen_notes,
        "allowed_sources": ["Finnhub company news", "Finnhub metrics", "price bars"],
    }
    if snap.ml_expected_return_pct is not None:
        payload["ml_prediction"] = {
            "expected_return_pct": snap.ml_expected_return_pct,
            "horizon_days": snap.ml_horizon_days,
            "confidence": snap.ml_confidence,
        }
    return payload


def _parse_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data
    return {}


def _f(data: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(data.get(key) if data.get(key) is not None else default)
    except (TypeError, ValueError):
        return default


def _i(data: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(data.get(key) if data.get(key) is not None else default)
    except (TypeError, ValueError):
        return default


def _decision_from_dict(
    data: dict[str, Any], ticker: str, horizon_days: int, price: float
) -> AiDecision:
    action = str(data.get("action") or "AVOID").upper()
    if action not in {"BUY", "HOLD", "AVOID", "SELL", "ADD"}:
        action = "AVOID"
    confidence = max(0.0, min(1.0, _f(data, "confidence")))
    risk_score = max(1, min(10, _i(data, "risk_score", 6)))
    hz = _i(data, "horizon_days", horizon_days) or horizon_days
    expected_days = _i(data, "expected_days", hz) or hz
    expected_return = _f(data, "expected_return_pct")
    expected_price = _f(data, "expected_price")
    if expected_price <= 0 and price > 0:
        expected_price = round(price * (1 + expected_return / 100.0), 4)
    if expected_return == 0 and expected_price > 0 and price > 0:
        expected_return = round((expected_price / price - 1) * 100, 2)
    reasoning = str(data.get("reasoning") or data.get("why_buy") or "")
    why_buy = str(data.get("why_buy") or "No buy case from available facts.")
    why_sell = str(data.get("why_sell") or "Sell if the setup fails or the time stop hits.")
    bullets = to_bullets(data.get("bullets") or [], why_buy, why_sell, reasoning, limit=4)
    return AiDecision(
        ticker=str(data.get("ticker") or ticker).upper(),
        action=action,  # type: ignore[arg-type]
        confidence=confidence,
        horizon_days=hz,
        why_buy=why_buy,
        why_sell=why_sell,
        risk_notes=str(data.get("risk_notes") or "Equity risk is never zero."),
        risk_score=risk_score,
        expected_days=expected_days,
        expected_price=expected_price,
        expected_return_pct=expected_return,
        reasoning=reasoning,
        bullets=bullets,
    )


def _fill_targets(snap: Snapshot, horizon_days: int, mode: str, action: str) -> tuple[int, float, float]:
    ret = 6.0 if mode == "short" else 10.0
    if action != "BUY":
        ret = max(snap.rs_10d, 0.0) if mode == "short" else max(snap.rs_20d, 0.0)
    price = snap.quote.price
    return horizon_days, round(price * (1 + ret / 100.0), 4), ret


def heuristic_decision(snap: Snapshot, horizon_days: int, mode: str) -> AiDecision:
    score = 0
    notes: list[str] = []
    sell_if: list[str] = []
    sent = snap.sentiment.news_score if snap.sentiment and snap.sentiment.news_score is not None else 0.0

    if snap.quote.price < 2:
        days, px, ret = _fill_targets(snap, horizon_days, mode, "AVOID")
        return AiDecision(
            ticker=snap.symbol,
            action="AVOID",
            confidence=0.8,
            horizon_days=horizon_days,
            why_buy="No buy. Price is below the $2 liquidity floor.",
            why_sell="Do not enter.",
            risk_notes="Penny-name risk and wide spreads.",
            risk_score=9,
            expected_days=days,
            expected_price=px,
            expected_return_pct=0.0,
            reasoning="Price is under the liquidity floor.",
            bullets=to_bullets("Price is under the $2 floor.", "Skip this name.", "Liquidity is too thin.", limit=4),
        )

    if snap.earnings_in_days is not None and snap.earnings_in_days <= 2:
        days, px, ret = _fill_targets(snap, horizon_days, mode, "AVOID")
        return AiDecision(
            ticker=snap.symbol,
            action="AVOID",
            confidence=0.75,
            horizon_days=horizon_days,
            why_buy="No buy. Earnings are inside two days; gap risk is too high.",
            why_sell="Do not enter into binary events.",
            risk_notes="Earnings gaps can skip stop-loss orders.",
            risk_score=8,
            expected_days=days,
            expected_price=px,
            expected_return_pct=0.0,
            reasoning="Earnings too close.",
            bullets=to_bullets("Earnings in 2 days or less.", "Gap risk is too high.", "Do not enter now.", limit=4),
        )

    if sent <= -0.35:
        notes.append("News sentiment is negative.")
        score -= 3
    elif sent >= 0.15:
        notes.append("News sentiment is constructive.")
        score += 2

    if mode == "short":
        if snap.rs_10d >= 3:
            notes.append(f"10-day relative strength is {snap.rs_10d:.1f}%.")
            score += 2
        elif snap.rs_10d <= -4:
            notes.append(f"10-day trend is weak ({snap.rs_10d:.1f}%).")
            score -= 2
        if snap.volume_ratio >= 1.3:
            notes.append("Volume is elevated vs the 10-day average.")
            score += 1
        if snap.above_sma20:
            notes.append("Price is above the 20-day average.")
            score += 1
        else:
            score -= 1
    else:
        if snap.above_sma50 and snap.rs_20d > 0:
            notes.append("Price is above the 50-day average with a positive 20-day trend.")
            score += 2
        if snap.metrics and snap.metrics.revenue_growth and snap.metrics.revenue_growth > 0.05:
            notes.append("Revenue growth is positive.")
            score += 1
        if snap.metrics and snap.metrics.debt_to_equity and snap.metrics.debt_to_equity > 2.5:
            notes.append("Leverage looks high.")
            score -= 1
        if snap.profile and snap.profile.market_cap >= 10_000_000_000:
            notes.append("Market cap is in a more established range.")
            score += 1
        elif snap.profile and snap.profile.market_cap and snap.profile.market_cap < 2_000_000_000:
            notes.append("This is still a smaller name for a 1-month hold.")
            score -= 1

    if snap.news:
        notes.append(f"Latest headline: {snap.news[0].headline}")
    if snap.ml_expected_return_pct is not None:
        notes.append(f"ML model scenario: {snap.ml_expected_return_pct:.1f}% (not a guarantee).")

    sell_if.append(f"Hard stop if price falls about {5 if mode == 'short' else 7}% from entry.")
    sell_if.append(f"Time stop after {horizon_days} trading days if the thesis has not played out.")
    sell_if.append("Sell on severe negative news (fraud, bankruptcy, regulatory charges).")
    sell_if.append("Take profit if the target is hit, then trail the rest.")

    if score >= 3:
        action = "BUY"
        confidence = min(0.78, 0.5 + score * 0.05)
        risk_score = 5 if mode == "swing" else 6
        why_buy = " ".join(notes) or "Setup meets the mechanical screen."
    elif score >= 1:
        action = "HOLD"
        confidence = 0.45
        risk_score = 6
        why_buy = "Mixed facts; wait rather than force a new buy. " + " ".join(notes)
    else:
        action = "AVOID"
        confidence = 0.6
        risk_score = 7
        why_buy = "Facts do not support a new buy. " + " ".join(notes)

    days, px, ret = _fill_targets(snap, horizon_days, mode, action)
    return AiDecision(
        ticker=snap.symbol,
        action=action,  # type: ignore[arg-type]
        confidence=round(confidence, 2),
        horizon_days=horizon_days,
        why_buy=why_buy,
        why_sell=" ".join(sell_if),
        risk_notes="Stops can be gapped through. Never risk more than the sized slice of allocated capital. Targets are scenarios, not promises.",
        risk_score=risk_score,
        expected_days=days,
        expected_price=px,
        expected_return_pct=ret if action == "BUY" else 0.0,
        reasoning=why_buy,
        bullets=to_bullets(notes[:3], sell_if[:1], limit=4),
    )


def heuristic_position(
    pos: Position, snap: Snapshot, engine_reason: str | None, horizon_days: int
) -> AiDecision:
    price = snap.quote.price
    pnl_pct = (price / pos.entry_price - 1) * 100 if pos.entry_price else 0
    if engine_reason:
        action = "SELL"
        reasoning = f"Risk engine exit: {engine_reason}. Unrealized {pnl_pct:+.1f}%."
        confidence = 0.8
    elif pnl_pct >= (pos.take_profit / pos.entry_price - 1) * 100 * 0.9:
        action = "SELL"
        reasoning = f"Close to take-profit. Unrealized {pnl_pct:+.1f}%."
        confidence = 0.7
    elif pos.age_days() >= pos.horizon_days:
        action = "SELL"
        reasoning = "Time stop: horizon used up."
        confidence = 0.7
    elif pnl_pct > 2 and snap.above_sma20:
        action = "ADD"
        reasoning = f"Thesis working. Unrealized {pnl_pct:+.1f}%. Consider buying a little more."
        confidence = 0.62
    else:
        action = "HOLD"
        reasoning = f"Watch this name. Age {pos.age_days()}d, unrealized {pnl_pct:+.1f}%."
        confidence = 0.55
    remaining = max(1, pos.horizon_days - pos.age_days())
    bullets = to_bullets(
        reasoning,
        f"Stop ${pos.stop_price:.2f}.",
        pos.why_sell or "Sell if thesis breaks or time stop hits.",
        limit=4,
    )
    return AiDecision(
        ticker=pos.symbol,
        action=action,  # type: ignore[arg-type]
        confidence=confidence,
        horizon_days=remaining,
        why_buy=pos.why_buy,
        why_sell=pos.why_sell or reasoning,
        risk_notes="Human approval is required before any sell is sent.",
        risk_score=6,
        expected_days=remaining,
        expected_price=pos.take_profit or price,
        expected_return_pct=round((pos.take_profit / price - 1) * 100, 2) if price else 0,
        reasoning=reasoning,
        bullets=bullets,
    )
