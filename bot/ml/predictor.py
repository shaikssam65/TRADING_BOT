from __future__ import annotations

from dataclasses import dataclass

from bot.models import Snapshot


@dataclass
class MLPrediction:
    expected_return_pct: float
    horizon_days: int
    confidence: float
    source: str = "ml"


class Predictor:
    """Swap this later for a trained stock-prediction model.

    Return None to skip. The AI advisor will include any prediction in its facts.
    """

    def predict(self, snap: Snapshot) -> MLPrediction | None:
        return None


class NullPredictor(Predictor):
    pass


def default_predictor() -> Predictor:
    return NullPredictor()
