from __future__ import annotations

from bot.strategies.short import SHORT_UNIVERSE
from bot.strategies.swing import SWING_UNIVERSE

# Established US names for "Top 20 overall".
TOP20 = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "AVGO",
    "JPM",
    "V",
    "UNH",
    "XOM",
    "JNJ",
    "WMT",
    "PG",
    "MA",
    "HD",
    "COST",
    "ORCL",
    "LLY",
]

# Liquid names we quote, then keep only if live price is under $10 or under $100.
BAND_CANDIDATES = list(
    dict.fromkeys(
        SHORT_UNIVERSE
        + SWING_UNIVERSE
        + [
            "F",
            "BAC",
            "INTC",
            "PFE",
            "T",
            "VZ",
            "NKE",
            "DIS",
            "PYPL",
            "UBER",
            "WBD",
            "AAL",
            "CCL",
            "NU",
            "SOFI",
            "HOOD",
            "RIVN",
            "SNAP",
            "PLTR",
            "NIO",
            "GOLD",
            "KO",
            "PEP",
            "CSCO",
            "IBM",
            "GE",
            "BA",
            "GM",
            "SIRI",
            "NOK",
            "VALE",
            "ITUB",
            "BBD",
            "ERIC",
            "LCID",
            "OPEN",
            "DKNG",
            "U",
            "PATH",
            "MARA",
            "RIOT",
            "JOBY",
            "ASTS",
            "HIMS",
            "PINS",
            "LYFT",
            "WBA",
            "PARA",
            "KEY",
            "HBAN",
            "RF",
        ]
    )
)

FILTER_LABELS = {
    "top10_under_10": "Top 10 best stocks below $10",
    "top10_under_100": "Top 10 best stocks below $100",
    "top20_overall": "Top 20 best stocks overall",
}

ALL_FILTERS = ("top10_under_10", "top10_under_100", "top20_overall")

RESULT_COUNTS = {
    "top10_under_10": 10,
    "top10_under_100": 10,
    "top20_overall": 20,
}


def result_count(filters: list[str] | None) -> int:
    selected = [f for f in (filters or []) if f in ALL_FILTERS]
    if not selected:
        return 10
    return max(RESULT_COUNTS.get(f, 10) for f in selected)


def symbols_for_filters(filters: list[str] | None) -> list[str]:
    selected = [f for f in (filters or []) if f in ALL_FILTERS]
    if not selected:
        return []
    out: list[str] = []
    if "top20_overall" in selected:
        out.extend(TOP20)
    if any(f in selected for f in ("top10_under_10", "top10_under_100")):
        out.extend(BAND_CANDIDATES)
    return list(dict.fromkeys(out))


def matches_filters(symbol: str, price: float, filters: list[str] | None) -> bool:
    selected = [f for f in (filters or []) if f in ALL_FILTERS]
    if not selected:
        return True
    if "top20_overall" in selected and symbol.upper() in TOP20:
        return True
    if "top10_under_10" in selected and 2 <= price < 10:
        return True
    if "top10_under_100" in selected and 2 <= price < 100:
        return True
    return False
