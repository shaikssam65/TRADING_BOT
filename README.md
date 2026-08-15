# USA AI Trading Bot (Webull)

A local app that **suggests** US stocks, **you approve** every order, then it **saves** the book so tomorrow it is still there.

**It cannot promise profit.** Stops can gap. AI can be wrong. You only risk the cash you type in.

Charts and extra detail: [FLOW.md](FLOW.md). Deploy: [DEPLOY.md](DEPLOY.md).

---

## What this does

- Screens US stocks from **live prices and news** (Finnhub).
- Writes **3–4 short bullets**: why buy, why sell, how many days, scenario P&L.
- **You** approve or reject. Nothing is sent until you click Execute.
- **Monitor** holdings: P&L per stock, plus Watch / Buy more / Sell.
- **Saves to disk** (`data/bot_state.json`) and **loads next day**.
- Without Webull keys, Execute is **practice only** (paper book on this PC). Real Webull money does not move.

---

## How each feature works

### Suggest buys
- Pick one list: **top 10 under $10**, **top 10 under $100**, or **top 20 overall**.
- Pick **short** (~10 days) or **swing** (~1 month).
- Click **Get suggestions**. Finnhub scores names; AI writes bullets.
- Each card shows price, shares, stop, target, and **scenario P&L** (if target hits / if stop hits).
- Ideas stay saved until you click Get suggestions again (that replaces undecided buys).

### Approve
- Check the rows you want. **Approve** or **Reject**.
- Check “I understand this can lose money,” then **Execute**.
- No Webull API → fill is only in the local paper book.
- Webull sandbox → practice at the broker. **Live** → real money.

### Monitor
- Open positions **reload from the saved file**.
- Table: entry, now, **P&L $**, **P&L %**, age, stop, target.
- Closed trades show **realized P&L**.
- **Refresh AI monitor**: Watch / hold, Buy more, or Sell (still needs Approve).

### Saved book
- File: `data/bot_state.json` (this PC only; not git).
- Next day you still have: holdings, pending approvals, P&L, closed trades.
- Do not delete that file if you want history.

### Capital and risk
- You type the cash (default **$1,000**). The bot **never raises it**.
- Default: max **3** names, max **25%** per name, **20%** cash left unused.
- **Short:** stop ~−5%, target ~+6%, hold at least 2 days, time stop ~10 days.
- **Swing:** stop ~−7%, target ~+10%, hold at least 5 days, time stop ~22 days.
- Also: trailing stop after a gain, daily kill switch (~−3%), no same-day round trips, block on bad headlines (fraud / bankruptcy / SEC).
- If a risk rule fails, the idea still shows but **shares = 0**.

### How buy vs sell is chosen
- **Screen** ranks names (momentum, volume, moving averages, news, earnings).
- **AI** says BUY / Skip / Watch / Sell from those facts only. It does not invent headlines.
- **Risk engine** can veto a buy. AI cannot override it.
- **You** still must Approve.
- **Sell** if: stop, trail, take-profit, time used up, or severe news. Forced exits can skip the min-hold.

### APIs
- **Finnhub** — free key. Prices and news. Required. Do not buy the paid plan for this.
- **OpenAI** — pay-as-you-go (not ChatGPT Plus). Optional; without it, a weaker built-in score is used.
- **Webull** — free API for account holders (~$100 in the account). Only needed for real orders.

---

## Run the UI

```powershell
cd D:\TRADING_BOT
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m bot ui
```

Or: `streamlit run app.py`

Put keys in `.env`:

- `FINNHUB_API_KEY` — required
- `OPENAI_API_KEY` / `OPENAI_MODEL=gpt-5.6` — for AI bullets
- `WEBULL_APP_KEY` / `WEBULL_APP_SECRET` — only when you want broker orders

**Never put `.env` on GitHub.** On this PC the keys stay in `.env`. On Streamlit Cloud, paste the same keys in **App settings → Secrets** (not a file upload in the app).

Never commit `.env`. Restart the UI after code changes (`Ctrl+C`, then the same command).

Webull keys: website (not the phone app) → avatar → **Developer Tool** → **My Application**. Then:

```bash
pip install webull-openapi-python-sdk
```

---

## CLI (optional)

```bash
python -m bot suggest --capital 1000 --mode short
python -m bot run --capital 1000 --mode short
python -m bot set-capital 2500
python -m bot status
```

---

## Project layout

- `app.py` / `bot/ui/app.py` — screens
- `bot/service.py` — suggest, approve, execute, monitor
- `bot/ai/` — ChatGPT or heuristic
- `bot/risk/` — size, stops, PDT, kill switch
- `bot/data/` — Finnhub (+ Webull quotes if connected)
- `bot/broker/` — Webull or local paper fills
- `bot/ml/predictor.py` — future ML hook
- `data/bot_state.json` — saved book

Not in v1: options, crypto, after-hours entries, unsupervised live trading.
