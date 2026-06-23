# Engine container (PLAN §7.1) — runs the read-only API + scheduler on the always-on box
# (e.g. Oracle Cloud Always-Free, Mumbai). The Next.js dashboard deploys separately (Vercel).
FROM python:3.12-slim

WORKDIR /app

# System deps kept minimal; wheels cover numpy/pandas/pyarrow on slim.
RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml requirements.txt ./
COPY signal_engine ./signal_engine
COPY config ./config

# Install the package + API extras (+ feedparser/yfinance/apscheduler for live sources/sched).
RUN pip install --no-cache-dir -e ".[api]" feedparser yfinance apscheduler

# Non-secret defaults; real secrets come from the environment / .env at runtime (never baked in).
ENV SE_DATA_SOURCE=mock \
    SE_NEWS_SOURCE=mock \
    SE_CUES_SOURCE=mock \
    SE_ALLOW_LIVE_ORDERS=false

EXPOSE 8000

# Default: serve the API. Override the command to run the scheduler instead.
CMD ["python", "-m", "signal_engine.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
