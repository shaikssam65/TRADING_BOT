from __future__ import annotations

from bot.broker.paper import PaperBroker
from bot.broker.webull_broker import WebullBroker
from bot.config import Settings


def build_broker(settings: Settings, *, force_paper: bool = False):
    if force_paper or not settings.execute:
        return PaperBroker()
    if not settings.webull_app_key or not settings.webull_app_secret:
        raise RuntimeError(
            "Execution needs WEBULL_APP_KEY and WEBULL_APP_SECRET in .env. "
            "Apply on the Webull website: avatar -> Developer Tool -> My Application."
        )
    return WebullBroker(
        app_key=settings.webull_app_key,
        app_secret=settings.webull_app_secret,
        account_id=settings.webull_account_id,
        live=settings.live,
    )
