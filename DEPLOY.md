# Deploy the USA AI Trading Bot

Do **not** put this on a public URL without `APP_PASSWORD`. Do **not** commit `.env`. Live Webull trading from a public site is dangerous.

## Free options (honest)

This bot **saves the book to a file** (`data/bot_state.json`). Most free cloud hosts **wipe that file** when the app sleeps or restarts. For save/load to work, prefer this PC.

| Where | Cost | Public URL? | Book saved overnight? |
| --- | --- | --- | --- |
| **This PC** (`python -m bot ui`) | Free | No (localhost) | **Yes** |
| **This PC + Cloudflare Tunnel** | Free | Yes | **Yes** (file stays on your PC) |
| **Streamlit Community Cloud** | Free | Yes (`*.streamlit.app`) | **No** — disk is wiped on sleep/redeploy |
| Hugging Face Spaces (free) | Free | Yes | Usually **no** unless you pay for persistent disk |

**Best free choice:** keep running it on this computer.

**Best free public URL:** leave the app running on this PC and expose it with [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (`cloudflared`). Set `APP_PASSWORD` first.

**Streamlit Cloud** is fine to *try* the UI in a browser. Treat it as a demo: holdings and P&L will not reliably come back tomorrow. Steps are under Option C below.

## What must be set

In `.env` (or Streamlit Cloud secrets):

- `FINNHUB_API_KEY` — required for suggestions
- `OPENAI_API_KEY` — ChatGPT reasons
- `OPENAI_MODEL=gpt-5.6`
- `APP_PASSWORD` — required if anyone else can open the URL
- `WEBULL_APP_KEY` / `WEBULL_APP_SECRET` — only when you want real broker orders

---

## Option A — This PC (simplest)

Keep using VS Code:

```powershell
cd D:\TRADING_BOT
.\.venv\Scripts\activate
python -m bot ui
```

Open `http://localhost:8501`.

To start it when Windows boots: Task Scheduler → Create Task → trigger At log on → action Start a program:

- Program: `D:\TRADING_BOT\.venv\Scripts\python.exe`
- Arguments: `-m streamlit run app.py --server.headless true`
- Start in: `D:\TRADING_BOT`

This is not “the internet.” Only your computer can open it unless you port-forward (don’t).

---

## Option B — Docker on this PC or a VPS

Need [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```powershell
cd D:\TRADING_BOT
docker compose up --build -d
```

Open `http://localhost:8501`.

Set `APP_PASSWORD` in `.env` first. Stop with `docker compose down`.

On a VPS (DigitalOcean, etc.): copy the project, create `.env` on the server, run the same compose command, put HTTPS (Caddy/Nginx) in front of port 8501.

---

## Option C — Streamlit Community Cloud

1. Put the project on GitHub (**no `.env`**).
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app → pick the repo → main file `app.py`.
3. App settings → Secrets → paste:

```toml
FINNHUB_API_KEY = "your_finnhub_key"
OPENAI_API_KEY = "your_openai_key"
OPENAI_MODEL = "gpt-5.6"
APP_PASSWORD = "pick_a_strong_password"
WEBULL_APP_KEY = ""
WEBULL_APP_SECRET = ""
```

4. Deploy. You get a public `https://....streamlit.app` URL. Sign in with `APP_PASSWORD`.

**Do not** add `.env` to the GitHub repo. **Do not** upload `.env` inside the app. Streamlit Cloud reads **Secrets** (step 3). Those stay on Streamlit, not in git.

Without Webull keys, Approve still uses the **paper book** (simulation), not your brokerage.

---

## After it is up

1. Sign in (if password is set).
2. Pick a suggestion type → **Get suggestions**.
3. **Approve** → execute (paper until Webull is connected).
4. **Monitor** → Refresh AI monitor.

See [FLOW.md](FLOW.md) for the diagrams.
