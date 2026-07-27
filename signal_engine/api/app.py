"""FastAPI engine API (PLAN §7.1, §11.6).

Read-only endpoints the Vercel/Next.js dashboard consumes over HTTPS + WebSocket. The
engine runs on the always-on box; this app exposes its data. There is NO order endpoint —
this is a decision-support tool.

Auth: if env ``SE_API_TOKEN`` is set, every /api route requires header ``X-API-Key`` to match
(open in local dev when unset). CORS is permissive for the (separate-origin) Vercel app;
lock ``allow_origins`` to your dashboard URL in production.
"""

from __future__ import annotations

import asyncio
import functools
import os
import threading
import time as _time
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout
from datetime import date, datetime, time

import pytz
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from signal_engine.api import scans
from signal_engine.api.serializers import chart_to_json, leaderboard_to_json
from signal_engine.config import load_config
from signal_engine.data.synthetic import bars_to_ticks, generate_session
from signal_engine.market.calendar import NSECalendar

_IST = pytz.timezone("Asia/Kolkata")

_DISCLAIMER = (
    "Decision-support only. Not investment advice. No live orders are placed. "
    "Intraday trading carries substantial risk of loss. (PLAN §9)"
)

# Single in-flight Dhan login (server-side, TTL-guarded) so a stray /consume can't inject a
# token without a recent /start. Personal single-user tool; see auth_consume for the rationale.
_PENDING_AUTH: dict = {}
_AUTH_TTL_S = 600

# --- Heavy scans: one subprocess, one in-flight computation per key --------------------
#
# /api/leaderboard (74 s cold), /api/premarket and /api/backtest (623 s for the default 10
# days on a 40-name watchlist) are pure-Python CPU. Measured on 2026-07-26 with a backtest in
# flight: /healthz still answered in 0.5 s, but /api/portfolio timed out past 60 s. Sync
# handlers are already OFF the event loop — FastAPI runs them on Starlette's threadpool — so
# the threadpool was never the problem. The problem is the GIL: a SELECT-heavy endpoint
# reacquires it thousands of times and loses every race to the scan thread, which is exactly
# the reported symptom (health fine, every data endpoint dead). A separate *process* is the
# only thing that keeps the API's interpreter free, so that is what these run in.
#
# Single-flight matters as much as isolation: the cache was only written after a scan
# finished, so two dashboard tabs asking for the same cold key each ran the full 74 s scan
# (observed as duplicate "real scan: 517/600" log lines at 150 % CPU).
#
# One worker per LANE, not one worker overall. A backtest runs for minutes (the 623 s above),
# and with a single shared worker every cold read-path key — leaderboard, premarket, movers —
# queued behind it and 503'd for the whole run. The read path is what the dashboard blocks on,
# so it gets a lane of its own. Each lane's worker is spawned on first use, so the backtest
# worker only exists once someone actually runs a backtest, and an idle one costs 0 % CPU.
_SCAN_LANE = "scan"             # leaderboard / premarket / movers / startup warm
_BACKTEST_LANE = "backtest"     # /api/backtest only — minutes-long, must not starve the above
_SCAN_POOLS: dict = {}          # lane -> ProcessPoolExecutor(max_workers=1)
_SCAN_POOL_LOCK = threading.Lock()
_SCAN_WAIT_S = 20.0     # how long a caller waits when there is NOTHING cached to serve yet


def _new_scan_pool() -> ProcessPoolExecutor:
    """A lane's worker: one process, and it exits with us (macOS has no PDEATHSIG)."""
    return ProcessPoolExecutor(max_workers=1, initializer=scans.exit_with_parent)


def _submit_scan(fn, *args, lane: str = _SCAN_LANE):
    """Run ``fn(*args)`` in ``lane``'s scan subprocess and return its Future.

    ``fn`` must be a module-level function in ``signal_engine.api.scans`` (it is pickled to the
    child) returning JSON-ready data. One worker per lane, so work within a lane stays
    serialised — two concurrent scans in the same lane would put us back to two busy cores on a
    laptop that also runs the live engine. Tests replace this with an inline runner so the suite
    never spawns a process."""
    with _SCAN_POOL_LOCK:
        pool = _SCAN_POOLS.get(lane)
        if pool is None:
            pool = _SCAN_POOLS[lane] = _new_scan_pool()
        try:
            return pool.submit(fn, *args)
        except BrokenExecutor:       # child was killed (OOM, stray SIGKILL) — rebuild once
            pool = _SCAN_POOLS[lane] = _new_scan_pool()
            return pool.submit(fn, *args)


def _shutdown_scan_pool() -> None:
    """Drop queued scans on shutdown; a scan already running finishes in the child. If this
    process is killed outright instead, the child's parent-watchdog takes it down within ~2 s
    (see ``scans.exit_with_parent``), so no path leaves a scan worker spinning."""
    with _SCAN_POOL_LOCK:
        for pool in _SCAN_POOLS.values():
            pool.shutdown(wait=False, cancel_futures=True)
        _SCAN_POOLS.clear()


_NO_VALUE = object()


class _ScanCache:
    """TTL cache with single-flight refresh and stale-while-revalidating reads.

    A request never triggers more than one computation per key, and while a refresh runs the
    previous payload is served immediately rather than making the caller wait behind it. Only a
    caller with nothing at all to serve blocks, and only for ``wait_s`` — past that it gets a
    503 telling it to retry, which beats holding a connection open through a Cloudflare quick
    tunnel that gives up at ~100 s anyway."""

    def __init__(self, name: str, ttl: float, max_entries: int = 16, lane: str = _SCAN_LANE):
        self._name = name
        self._ttl = ttl
        self._max_entries = max_entries
        self._lane = lane
        # Re-entrant: an inline runner (tests) completes the future before add_done_callback
        # returns, so _settle re-enters on the same thread.
        self._lock = threading.RLock()
        self._entries: dict = {}     # key -> (computed_epoch, payload)
        self._inflight: dict = {}    # key -> Future

    def _fresh(self, key):
        hit = self._entries.get(key)
        if hit is not None and _time.time() - hit[0] < self._ttl:
            return hit[1]
        return _NO_VALUE

    def _settle(self, key, fut) -> None:
        with self._lock:
            if self._inflight.get(key) is fut:
                self._inflight.pop(key, None)
            try:
                value = fut.result()
            except Exception:  # noqa: BLE001 - the waiting caller reports it; keep the old payload
                return
            if key not in self._entries and len(self._entries) >= self._max_entries:
                self._entries.pop(min(self._entries, key=lambda k: self._entries[k][0]), None)
            self._entries[key] = (_time.time(), value)

    def prime(self, key, fn, *args):
        """Ensure a computation for `key` is running. Never blocks; returns the Future."""
        with self._lock:
            fut = self._inflight.get(key)
            if fut is None:
                fut = _submit_scan(fn, *args, lane=self._lane)
                self._inflight[key] = fut
                fut.add_done_callback(functools.partial(self._settle, key))
            return fut

    def get(self, key, fn, *args, wait_s: float = _SCAN_WAIT_S):
        with self._lock:
            fresh = self._fresh(key)
            if fresh is not _NO_VALUE:
                return fresh
            fut = self.prime(key, fn, *args)
            fresh = self._fresh(key)          # an inline runner has already settled it
            if fresh is not _NO_VALUE:
                return fresh
            hit = self._entries.get(key)
            stale = hit[1] if hit is not None else _NO_VALUE
        if stale is not _NO_VALUE:
            return stale                      # serve the last good payload; refresh runs behind it
        try:
            return fut.result(timeout=wait_s)
        except _FutureTimeout:
            raise HTTPException(
                503, f"{self._name} is still computing (first run after a restart) — retry shortly",
                headers={"Retry-After": "30"}) from None
        except Exception as exc:  # noqa: BLE001 - surface the child's failure, don't 500 silently
            raise HTTPException(503, f"{self._name} scan failed: {exc}") from None


def _watchlist_sectors() -> dict:
    """Parse the inline ``# sector / note`` comment for each watchlist symbol straight from
    config/settings.yaml (the watchlist is a plain list of strings in the typed config, so the
    human-readable sector tags only live in the YAML comments). Best-effort: returns {} on any
    read error. e.g. `- "RELIANCE"   # energy / conglomerate` -> {"RELIANCE": "energy / ..."}."""
    import re

    from signal_engine.config import DEFAULT_CONFIG_DIR

    out: dict = {}
    try:
        text = (DEFAULT_CONFIG_DIR / "settings.yaml").read_text()
    except OSError:
        return out
    pat = re.compile(r'-\s*"([^"]+)"\s*#\s*(.+?)\s*$')
    in_watchlist = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("watchlist:"):
            in_watchlist = True
            continue
        if in_watchlist:
            # The watchlist block ends at the next top-level (non-indented, non-comment) key.
            if line and not line[0].isspace() and not stripped.startswith("#"):
                break
            m = pat.search(line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def _require_token(x_api_key: str = Header(default=None)) -> None:
    expected = os.getenv("SE_API_TOKEN")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


def _parse_date(s: str = None) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date() if s else date(2025, 6, 23)


class _QuoteHub:
    """One shared Dhan-REST LTP poller for ALL ``/ws/quotes`` connections (latency + rate-limit fix).

    BEFORE: every WebSocket connection built its own broker (≈3.4 s cold start — the scrip master is
    re-downloaded each ``build_broker``) and polled Dhan independently. Dhan's quote REST is ≈1 req/s,
    so a *second* concurrent connection (watchlist + a stock chart, or two tabs) blew past the quota
    and got DH-904/429'd — the dashboard then skipped that update, so the live price stalled for
    several seconds. Measured: a lone connection streams cleanly at ~1.1 s; a second consumer was
    throttled on 16/16 polls.

    AFTER: a single background task polls the UNION of every connection's symbols once per
    ``POLL_INTERVAL`` and all connections read from the warm cache. N connections → 1 Dhan poll,
    instant first tick (cache already warm), zero self-collision. The broker is built once and shared
    (its HTTP layer is stateless ``urllib`` per call, so the poll loop and the intraday endpoint can
    use it concurrently). Read-only — never places orders.
    """

    POLL_INTERVAL = 1.0          # Dhan quote REST is ≈1 req/s; one poller stays comfortably under it.

    def __init__(self, cfg):
        self._cfg = cfg
        self._subs: dict = {}        # conn_id -> set[str]  (empty set = wants the full watchlist)
        self._cache: dict = {}       # symbol -> ltp (latest)
        self._ts: int = 0            # epoch secs of the last successful poll
        self._last_error = None
        self._broker = None
        self._broker_token = None
        self._watchlist: list = []
        self._task = None
        self._next_id = 0
        self._broker_lock = threading.Lock()

    # --- shared broker (reused by the poll loop AND /api/intraday) ----------
    def broker(self):
        """Thread-safe shared DhanBroker, rebuilt only when the (TOTP-rotated) token changes.

        Sync + lock-guarded so the async poll loop (via ``to_thread``) and the intraday request
        handler (threadpool) build the scrip master at most once between token rotations."""
        from signal_engine.config import load_config as _load
        from signal_engine.config import refresh_runtime_env, resolve_live_watchlist
        from signal_engine.factory import build_broker

        refresh_runtime_env()                       # pick up the freshest token from .env
        tok = os.getenv("DHAN_ACCESS_TOKEN")
        with self._broker_lock:
            if self._broker is None or tok != self._broker_token:
                live_cfg = _load()
                self._watchlist = resolve_live_watchlist(live_cfg)
                self._broker = build_broker(live_cfg, datetime.now(_IST).date())
                self._broker_token = tok
            return self._broker

    # --- subscription registry ---------------------------------------------
    def subscribe(self, symbols) -> int:
        cid = self._next_id
        self._next_id += 1
        self._subs[cid] = set(symbols or [])        # empty set ⇒ full watchlist
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())
        return cid

    def unsubscribe(self, cid) -> None:
        self._subs.pop(cid, None)

    def _union(self):
        want_all = any(not s for s in self._subs.values())
        u = set(self._watchlist) if want_all else set()
        for s in self._subs.values():
            u.update(s)
        return sorted(u)[:100]                       # Dhan caps a batch; the watchlist is ~40

    def slice(self, symbols):
        if symbols:
            return {s: self._cache[s] for s in symbols if s in self._cache}
        return dict(self._cache)

    # --- the single poll loop ----------------------------------------------
    async def _run(self) -> None:
        backoff = 0.0
        while self._subs:
            try:
                broker = await asyncio.to_thread(self.broker)   # builds _watchlist on first call
                syms = self._union()
                if syms:
                    quotes = await asyncio.to_thread(broker.quote, syms)
                    self._cache.update({s: round(t.ltp, 2) for s, t in quotes.items() if t.ltp})
                    self._ts = int(datetime.now().timestamp())
                    self._last_error = None
                backoff = 0.0
            except Exception as exc:  # noqa: BLE001 - one bad poll must not kill the shared loop
                msg = str(exc)
                if "DH-904" in msg or "429" in msg or "Rate_Limit" in msg:
                    self._last_error = "Dhan rate limit (DH-904)"
                    backoff = min(backoff + 0.5, 3.0)
                else:
                    self._last_error = msg[:200]
                    backoff = min(backoff + 1.0, 5.0)
            await asyncio.sleep(self.POLL_INTERVAL + backoff)
        self._task = None


def create_app() -> FastAPI:
    cfg = load_config()
    cal = NSECalendar()
    quote_hub = _QuoteHub(cfg)   # one shared LTP poller for every /ws/quotes connection
    app = FastAPI(title="Intraday Signal Engine API", version="0.1.0",
                  description="Read-only signal/decision-support API. No order placement.")

    # Lock this to your Vercel URL in production (env SE_CORS_ORIGINS, comma-separated).
    origins = os.getenv("SE_CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"],
    )

    @app.get("/")
    def root():
        return {"name": "intraday-signal-engine", "version": "0.1.0",
                "disclaimer": _DISCLAIMER, "live_orders": False}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # --- Dhan auth (in-dashboard OTP login) --------------------------------
    @app.get("/api/auth/status")
    def auth_status():
        """Token health for the dashboard gate. Public (it gates everything else)."""
        from signal_engine.brokers.dhan import token_expiry
        from signal_engine.config import refresh_runtime_env

        source = os.getenv("SE_DATA_SOURCE", "mock")
        if source != "dhan":
            return {"source": source, "auth_required": False, "connected": True}
        # Hot-reload the token from .env before reporting: the scheduler renews it DAILY in a
        # SEPARATE process, so this uvicorn process's startup env goes stale and the dashboard would
        # otherwise falsely show 'disconnected' after every nightly renewal (until a tunnel restart).
        # refresh_runtime_env only touches the volatile auth keys and is best-effort — it can never
        # break the endpoint, and it ends the nightly staleness at its root.
        try:
            refresh_runtime_env()
        except Exception:  # noqa: BLE001
            pass
        exp = token_expiry(os.getenv("DHAN_ACCESS_TOKEN") or "")
        connected = bool(exp and exp > datetime.utcnow())
        return {"source": "dhan", "auth_required": True, "connected": connected,
                "expires_at": (exp.isoformat() + "Z") if exp else None,
                "login_path": "/api/auth/dhan/start"}

    @app.get("/api/auth/dhan/start")
    def auth_start():
        """Begin the consent flow and 302 the browser to Dhan's OTP page."""
        import time as _t

        from signal_engine.brokers import dhan_auth

        cid, key, sec = (os.getenv("DHAN_CLIENT_ID"), os.getenv("DHAN_API_KEY"),
                         os.getenv("DHAN_API_SECRET"))
        if not (cid and key and sec):
            raise HTTPException(400, "Dhan consent login not configured "
                                     "(set DHAN_API_KEY / DHAN_API_SECRET / DHAN_CLIENT_ID).")
        try:
            consent = dhan_auth.generate_consent(cid, key, sec)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"generate-consent failed: {exc}") from exc
        _PENDING_AUTH["pending"] = {"consent": consent, "ts": _t.time()}
        return RedirectResponse(dhan_auth.consent_login_url(consent), status_code=302)

    @app.get("/api/auth/dhan/consume")
    def auth_consume(tokenId: str = Query(...)):
        """Exchange the post-OTP tokenId for a fresh token and persist it. Called by the
        Vercel callback route (server-side). Requires a recent /start (TTL + single-use)."""
        import time as _t

        from signal_engine.brokers import dhan_auth
        from signal_engine.brokers.dhan import token_expiry

        pend = _PENDING_AUTH.get("pending")
        if not pend or (_t.time() - pend["ts"]) > _AUTH_TTL_S:
            raise HTTPException(400, "No active login request (start one from the dashboard).")
        key, sec = os.getenv("DHAN_API_KEY"), os.getenv("DHAN_API_SECRET")
        try:
            tok = dhan_auth.consume_consent(tokenId, key, sec)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"consume-consent failed: {exc}") from exc
        dhan_auth.update_env_token(tok)
        os.environ["DHAN_ACCESS_TOKEN"] = tok  # this process uses it immediately
        _PENDING_AUTH.pop("pending", None)      # single-use
        exp = token_expiry(tok)
        return {"connected": True, "expires_at": (exp.isoformat() + "Z") if exp else None}

    # TTLs: how stale a payload may get before the next reader triggers a refresh. Nothing
    # recomputes on a timer — an untouched dashboard costs nothing.
    _leaderboard_cache = _ScanCache("leaderboard", ttl=900.0)
    _premarket_cache = _ScanCache("premarket briefing", ttl=300.0)
    _backtest_cache = _ScanCache("backtest", ttl=21600.0, lane=_BACKTEST_LANE)

    @app.get("/api/leaderboard", dependencies=[Depends(_require_token)])
    def leaderboard(
        date_str: str = Query(default=None, alias="date"),
        universe: int = Query(default=500, ge=10, le=3000),
        top: int = Query(default=20, ge=1, le=100),
        seed: int = 42, news: bool = True, ml: bool = False,
    ):
        # Real data: scan the backfilled NSE corpus (recognizable names, real prices). Falls back
        # to the synthetic universe only if the archive is empty or SE_DATA_SOURCE=mock.
        #
        # `top` is deliberately NOT part of the cache key. The expensive part is scanning the
        # corpus; the result is a ranked list that `top` only TRUNCATES, so the full ranking is
        # computed once and sliced per request — changing the dashboard's stock count is instant.
        # The 15-min TTL is what picks up a grown corpus (backfill, gap-fill, nightly archive)
        # without a restart; this is still the most-recent COMPLETE session, not a tick-live
        # ranking.
        if cfg.env.data_source != "mock":
            full = _leaderboard_cache.get((bool(news),), scans.archive_leaderboard, bool(news))
            if full is not None:                       # None = empty archive; use synthetic below
                return {**full, "entries": full["entries"][:top]}

        from signal_engine.scan.harness import run_scan
        from signal_engine.universe.mock import MockUniverseProvider

        d = _parse_date(date_str)
        if not cal.is_trading_day(d):
            raise HTTPException(400, f"{d} is not an NSE trading day")
        uni = MockUniverseProvider(n=universe, seed=seed)
        res = run_scan(cfg, uni, d, as_of=time(11, 0), seed=seed, top_n=top,
                       with_news=news, with_ml=ml)
        return leaderboard_to_json(res, d)

    @app.get("/api/premarket", dependencies=[Depends(_require_token)])
    def premarket(date_str: str = Query(default=None, alias="date"), seed: int = 42,
                  top: int = Query(default=40, ge=1, le=200),
                  universe: int = Query(default=150, ge=10, le=2000)):
        """Pre-open briefing over the most-liquid archived NSE names — see
        ``scans.premarket_briefing``. The real path re-fetches Yahoo cues + RSS news per call
        (measured 2.3–3.6 s) and a pre-open briefing doesn't change meaningfully inside 5 min,
        so the warm copy is served while a refresh runs behind it."""
        return _premarket_cache.get((date_str, seed, top, universe), scans.premarket_briefing,
                                    _parse_date(date_str), seed, top, universe)

    @app.get("/api/backtest", dependencies=[Depends(_require_token)])
    def backtest(
        start: str = Query(default=None), days: int = Query(default=10, ge=1, le=30),
        seed: int = 42,
    ):
        """Replay the watchlist over `days` sessions. Deterministic given (start, days, seed) —
        hence the long TTL; the result only moves when config or the calendar does.

        The ceiling is a CPU budget, not a modelling limit: a session-day costs ~62 s of one core
        (measured 623 s for days=10), so 30 caps an accidental run at ~30 min. It was 120 — a
        stray extra zero in the dashboard's Days box bought a ~2 h burn with no feedback. Raise
        it here and in web/app/backtest/page.tsx together if a longer window is ever wanted."""
        start_d = _parse_date(start) if start else date(2025, 6, 2)
        return _backtest_cache.get((start, days, seed), scans.backtest, start_d, days, seed)

    @app.get("/api/chart/{symbol}", dependencies=[Depends(_require_token)])
    def chart(symbol: str, date_str: str = Query(default=None, alias="date"), seed: int = 42):
        # Real data path: prefer the backfilled archive (full Dhan session, local/fast); if a
        # symbol isn't archived yet, fetch a real recent session from Yahoo rather than ever
        # showing synthetic bars dressed as real. Synthetic only in mock mode.
        if cfg.env.data_source != "mock":
            from signal_engine.storage.bars import ParquetBarStore

            df = ParquetBarStore(cfg.env.parquet_dir).load_latest_session(symbol.upper())
            if df is None or df.empty:
                try:
                    from signal_engine.data.yahoo_batch import fetch_intraday
                    df = fetch_intraday([symbol.upper()], interval="1m", period="1d").get(symbol.upper())
                except Exception:  # noqa: BLE001 - fall through to synthetic on any fetch error
                    df = None
            if df is not None and not df.empty:
                return chart_to_json(symbol, df, dict(cfg.settings.strategy.params))
        d = _parse_date(date_str)
        df = generate_session(symbol, d, seed=seed, regime="trend_up")
        return chart_to_json(symbol, df, dict(cfg.settings.strategy.params))

    _intraday_cache: dict = {}     # symbol -> (fetched_epoch, payload); short TTL (see below)
    _INTRADAY_TTL = 45.0           # seconds — keeps repeated page loads off the shared Dhan quota

    @app.get("/api/intraday/{symbol}", dependencies=[Depends(_require_token)])
    async def intraday(symbol: str):
        """TODAY's 1-minute price line (epoch secs + close) from the 09:15 open to now — used to
        SEED the live price chart so it shows the whole day, not just from when the page opened.

        Real-time source order: Dhan intraday (``broker.historical`` 1m, exact + live) → Yahoo 1d/1m
        (free, ~15 min delayed) → the archived latest session (last resort, may be a prior day).
        TTL-cached per symbol; the live ``/ws/quotes`` ticks extend it past the last bar on the
        client. Returns ``{symbol, points:[{time,value}], source, market_open}``."""
        import time as _t

        sym = symbol.upper()
        now = _t.time()
        hit = _intraday_cache.get(sym)
        if hit and now - hit[0] < _INTRADAY_TTL:
            return hit[1]

        points: list = []
        source = "none"
        if cfg.env.data_source == "dhan":
            try:
                today = datetime.now(_IST)
                start = _IST.localize(datetime.combine(today.date(), time(9, 15)))
                broker = await asyncio.to_thread(quote_hub.broker)   # shared; no scrip re-download
                bars = await asyncio.to_thread(broker.historical, sym, "1m", start, today)
                points = [{"time": int(b.ts.timestamp()), "value": round(float(b.close), 2)}
                          for b in bars if b.ts.date() == today.date()]
                if points:
                    source = "dhan"
            except Exception:  # noqa: BLE001 - fall through to Yahoo/archive on any error
                points = []
        if not points and cfg.env.data_source != "mock":
            try:
                from signal_engine.data.yahoo_batch import fetch_intraday
                df = await asyncio.to_thread(
                    lambda: fetch_intraday([sym], interval="1m", period="1d").get(sym))
                if df is not None and not df.empty:
                    points = [{"time": int(pd_ts.timestamp()), "value": round(float(c), 2)}
                              for pd_ts, c in df["close"].items()]
                    source = "yahoo"
            except Exception:  # noqa: BLE001
                points = []
        if not points and cfg.env.data_source != "mock":
            try:
                from signal_engine.storage.bars import ParquetBarStore
                df = ParquetBarStore(cfg.env.parquet_dir).load_latest_session(sym)
                if df is not None and not df.empty:
                    points = [{"time": int(pd_ts.timestamp()), "value": round(float(c), 2)}
                              for pd_ts, c in df["close"].items()]
                    source = "archive"
            except Exception:  # noqa: BLE001
                points = []

        now_ist = datetime.now(_IST)
        market_open = (cal.is_trading_day(now_ist.date())
                       and time(9, 15) <= now_ist.time() <= time(15, 30))
        payload = {"symbol": sym, "points": points, "source": source, "market_open": market_open}
        _intraday_cache[sym] = (now, payload)
        return payload

    # --- Paper-trading tracker & analytics ---------------------------------
    def _paper_enriched(start, end, symbol, strategy):
        """Load filtered paper trades from the DB and enrich with absolute P&L + charges."""
        from signal_engine.analytics import paper as pa
        from signal_engine.risk.costs import CostModel
        from signal_engine.storage.repository import SignalRepository

        repo = SignalRepository(cfg.env.db_url, read_only=True)
        try:
            rows = repo.fetch_trades(start=start, end=end, symbol=symbol, strategy=strategy)
        finally:
            repo.close()
        notional = float(cfg.risk.costs.reference_trade_value)
        enriched = pa.enrich_all(rows, notional, CostModel(cfg.risk.costs))
        # PORTFOLIO §7: prefer the REAL ledger ₹ (pnl_inr / charges_inr / qty from the book) when a
        # trade was sized by the portfolio; legacy rows (pnl_inr NULL) keep the modeled fixed-
        # notional figures and are flagged ``modeled: true`` so the UI can badge them.
        for e, row in zip(enriched, rows):
            real = row.get("pnl_inr") is not None
            e["modeled"] = not real
            if real:
                e["qty"] = int(row.get("qty") or e.get("qty") or 0)
                e["net_pnl_abs"] = round(float(row["pnl_inr"]), 2)
                e["costs_abs"] = round(float(row.get("charges_inr") or 0.0), 2)
                e["notional"] = round(float(row.get("notional_entry")
                                             or e.get("notional") or 0.0), 2)
        return enriched, notional

    @app.get("/api/paper/trades", dependencies=[Depends(_require_token)])
    def paper_trades(start: str = Query(default=None), end: str = Query(default=None),
                     symbol: str = Query(default=None), strategy: str = Query(default=None)):
        enriched, notional = _paper_enriched(start, end, symbol, strategy)
        return {"notional_per_trade": notional, "count": len(enriched), "trades": enriched}

    @app.get("/api/paper/analytics", dependencies=[Depends(_require_token)])
    def paper_analytics(start: str = Query(default=None), end: str = Query(default=None),
                        symbol: str = Query(default=None), strategy: str = Query(default=None)):
        from signal_engine.analytics import paper as pa

        enriched, notional = _paper_enriched(start, end, symbol, strategy)
        report = pa.full_report(enriched)
        report["notional_per_trade"] = notional
        report["account_capital"] = float(cfg.risk.risk.account_capital)
        # Which ₹ basis the aggregates rest on: 'ledger' once any real book-sized trade is present
        # (its actual pnl_inr flows through), else the modeled fixed-notional 'modeled' basis.
        report["basis"] = "ledger" if any(not e.get("modeled", True) for e in enriched) \
            else "modeled"
        return report

    def _open_positions_view():
        """Current open paper positions with entry/stop/target in BOTH ₹ (price) and % (move),
        plus live unrealized P&L (% and ₹ at the capital-agnostic reference notional)."""
        from signal_engine.storage.repository import SignalRepository

        notional = float(cfg.risk.costs.reference_trade_value)
        repo = SignalRepository(cfg.env.db_url, read_only=True)
        try:
            rows = repo.fetch_open_positions()
        finally:
            repo.close()
        out = []
        for r in rows:
            upnl_pct = r.get("unrealized_pnl_pct")
            out.append({
                "id": r["id"], "symbol": r["symbol"], "direction": r["direction"],
                "strategy": r["strategy"], "confidence": r["confidence"],
                "entry": r["entry_fill"], "entry_ts": r["entry_ts"],
                "stop_loss": r["stop_loss"], "stop_pct": r["stop_pct"],
                "target": r["target"], "target_pct": r["target_pct"],
                "risk_reward": r.get("risk_reward"),
                "expected_move_pct": r.get("expected_move_pct"),
                "last_price": r.get("last_price"),
                "unrealized_pnl_pct": upnl_pct,
                "unrealized_pnl_abs": (round(notional * upnl_pct / 100.0, 2)
                                       if upnl_pct is not None else None),
                "updated_ts": r.get("updated_ts"),
            })
        return out, notional

    @app.get("/api/paper/open", dependencies=[Depends(_require_token)])
    def paper_open():
        """Live open positions (entries currently in the market) — the piece the closed-trade
        views could never show. Refreshed each bar by the live loop."""
        positions, notional = _open_positions_view()
        return {"notional_per_trade": notional, "count": len(positions),
                "positions": positions}

    @app.get("/api/predictions", dependencies=[Depends(_require_token)])
    def predictions(limit: int = Query(default=200, ge=1, le=1000),
                    kind: str = Query(default=None), symbol: str = Query(default=None),
                    since_id: int = Query(default=None)):
        """The predictions log: every alert pushed to Telegram, newest first, with its
        structured parameters (entry/stop/target/confidence/sizing/exit P&L). ``since_id``
        lets the dashboard poll cheaply — pass the max id it has and merge the delta."""
        from signal_engine.storage.repository import SignalRepository

        repo = SignalRepository(cfg.env.db_url, read_only=True)
        try:
            rows = repo.fetch_predictions(limit=limit, kind=kind, symbol=symbol,
                                          since_id=since_id)
        finally:
            repo.close()
        return {"count": len(rows), "predictions": rows}

    @app.get("/api/live/status", dependencies=[Depends(_require_token)])
    def live_status():
        """Liveness beacon for the dashboard: when the live loop last processed a bar, how many
        positions are open, how many trades closed today. `stale` is True if no update in >180s
        (during market hours that means the feed is likely down)."""
        from signal_engine.storage.repository import SignalRepository

        repo = SignalRepository(cfg.env.db_url, read_only=True)
        try:
            st = repo.fetch_live_status()
        finally:
            repo.close()
        if not st:
            return {"live": False, "updated_ts": None, "open_count": 0, "closed_today": 0,
                    "bars_processed": 0, "watching": len(cfg.settings.watchlist),
                    "stale": True, "age_seconds": None}
        age = None
        try:
            from datetime import datetime as _dt
            updated = _dt.fromisoformat(st["updated_ts"])
            now = _dt.now(updated.tzinfo)
            age = (now - updated).total_seconds()
        except Exception:  # noqa: BLE001
            pass
        return {
            "live": True, "updated_ts": st["updated_ts"], "bar_ts": st.get("bar_ts"),
            "open_count": st.get("open_count", 0), "closed_today": st.get("closed_today", 0),
            "bars_processed": st.get("bars_processed", 0),
            "watching": st.get("watching", len(cfg.settings.watchlist)),
            "age_seconds": round(age) if age is not None else None,
            "stale": (age is not None and age > 180),
        }

    # --- Portfolio manager (the ₹1,00,000 paper book, PORTFOLIO §7) ---------
    def _portfolio_snapshot():
        """Read-only ₹-book view: equity / cash / invested, live open positions, today's and
        lifetime realized P&L, and a per-strategy breakdown — all derived from the DB. Never
        writes and never calls a broker (the live engine's ledger is the only writer). A fresh DB
        with no ``portfolio_state`` serves starting-capital defaults (HTTP 200, never 500)."""
        from signal_engine.storage.repository import SignalRepository, _now_iso

        start_default = float(getattr(cfg.risk.portfolio, "starting_capital", 100000.0))
        repo = SignalRepository(cfg.env.db_url, read_only=True)
        try:
            state = repo.fetch_portfolio_state()
            open_rows = repo.fetch_open_positions()
            today = _now_iso()[:10]
            realized_today = repo.sum_closed_pnl_inr(day=today)
            closed = repo.fetch_trades()
            curve = repo.fetch_equity_curve(days=30)
        finally:
            repo.close()

        starting = float(state["starting_capital"]) if state and state.get("starting_capital") \
            else start_default
        cash = float(state["cash"]) if state and state.get("cash") is not None else starting
        realized_total = float(state["realized_pnl_total"]) if state \
            and state.get("realized_pnl_total") is not None else 0.0

        positions, invested, unreal_total = [], 0.0, 0.0
        per_strat = {}

        def _strat(name):
            return per_strat.setdefault(name or "?", {"strategy": name or "?", "trades": 0,
                                                      "pnl_inr": 0.0, "invested_now": 0.0})

        for r in open_rows:
            qty = int(r.get("qty") or 0)
            if qty <= 0:
                continue  # unsized/legacy open row — not part of the ₹ book
            notional = float(r.get("notional") or 0.0)
            upnl_inr = r.get("unrealized_pnl_inr")
            upnl_inr = float(upnl_inr) if upnl_inr is not None else 0.0
            invested += notional
            unreal_total += upnl_inr
            _strat(r.get("strategy"))["invested_now"] += notional
            positions.append({
                "id": r["id"], "symbol": r["symbol"], "direction": r["direction"],
                "strategy": r["strategy"], "qty": qty, "entry_fill": r["entry_fill"],
                "last_price": r.get("last_price"), "notional": round(notional, 2),
                "unrealized_pnl_inr": round(upnl_inr, 2),
                "unrealized_pnl_pct": r.get("unrealized_pnl_pct"),
                "stop_loss": r.get("stop_loss"), "target": r.get("target"),
                "entry_ts": r.get("entry_ts"),
            })

        equity = cash + invested + unreal_total

        t_trades = t_wins = 0
        t_charges = 0.0
        for tr in closed:
            pnl = tr.get("pnl_inr")
            if pnl is None:
                continue  # legacy row — predates the book, excluded from ₹ sums
            d = _strat(tr.get("strategy"))
            d["trades"] += 1
            d["pnl_inr"] += float(pnl)
            if (tr.get("exit_ts") or "")[:10] == today:
                t_trades += 1
                t_charges += float(tr.get("charges_inr") or 0.0)
                if float(pnl) > 0:
                    t_wins += 1

        # Today's return baseline = the last equity point dated before today (yesterday's close),
        # else the starting capital on the book's very first day.
        baseline = starting
        for pt in curve:
            if (pt.get("day") or "") < today:
                baseline = float(pt["equity"])
        return {
            "starting_capital": round(starting, 2),
            "equity": round(equity, 2), "cash": round(cash, 2),
            "invested": round(invested, 2),
            "unrealized_pnl_inr": round(unreal_total, 2),
            "realized_pnl_today_inr": round(realized_today, 2),
            "realized_pnl_total_inr": round(realized_total, 2),
            "return_total_pct": round(100.0 * (equity - starting) / starting, 4) if starting else 0.0,
            "return_today_pct": round(100.0 * (equity - baseline) / baseline, 4) if baseline else 0.0,
            "open_positions": positions,
            "today": {"trades": t_trades, "wins": t_wins,
                      "pnl_inr": round(realized_today, 2), "charges_inr": round(t_charges, 2)},
            "per_strategy": [
                {"strategy": v["strategy"], "trades": v["trades"],
                 "pnl_inr": round(v["pnl_inr"], 2), "invested_now": round(v["invested_now"], 2)}
                for v in sorted(per_strat.values(), key=lambda x: -x["pnl_inr"])],
            "updated_ts": state.get("updated_ts") if state else None,
        }

    @app.get("/api/microstructure", dependencies=[Depends(_require_token)])
    def microstructure(day: str = Query(default="")):
        """Microstructure shadow signal (order-book imbalance + volume-delta) for a session,
        plus its next-bar directional hit rate vs the 50% coin-flip. Pure research — this
        signal NEVER gates a trade (see signal_engine/microstructure/)."""
        from datetime import datetime as _dt

        import pytz as _pytz

        from signal_engine.microstructure.store import MicrostructureStore

        d = day or _dt.now(_pytz.timezone("Asia/Kolkata")).date().isoformat()
        store = MicrostructureStore(cfg.env.db_url, read_only=True)
        try:
            rows = store.fetch_day(d)
        finally:
            store.close()
        scored = [r for r in rows if r.get("dir_correct") is not None]
        hits = sum(r["dir_correct"] for r in scored)
        return {
            "day": d, "n_signals": len(rows), "n_scored": len(scored),
            "hit_rate": (hits / len(scored)) if scored else None, "baseline": 0.5,
            "note": "shadow signal — logged + scored, never gates a trade",
            "rows": rows[:1000],
        }

    @app.get("/api/movers", dependencies=[Depends(_require_token)])
    def movers(days: int = Query(default=30, ge=1, le=120)):
        """Movers research sleeve: daily big-move predictions + realized outcomes + running
        scoreboard. Paper research only — measured base rates, honest labels (see
        signal_engine/movers/). Separate from the main paper book by design."""
        from signal_engine.storage.repository import SignalRepository

        repo = SignalRepository(cfg.env.db_url, read_only=True)
        try:
            rows = repo.fetch_mover_predictions(limit=days * 12)
        finally:
            repo.close()
        resolved = [r for r in rows if r.get("resolved_ts")]
        hits = [r for r in resolved if r.get("hit_big5")]
        dir_rows = [r for r in resolved if r.get("dir_correct") is not None]
        pnl_rows = [r for r in resolved if r.get("sleeve_pnl_pct") is not None]
        latest_day = rows[0]["for_day"] if rows else None
        return {
            "latest_day": latest_day,
            "today": [r for r in rows if r["for_day"] == latest_day],
            "history": rows,
            "scoreboard": {
                "n_predictions": len(rows),
                "n_resolved": len(resolved),
                "hit_rate_big5": (len(hits) / len(resolved)) if resolved else None,
                "base_rate_big5": 0.0491,  # 16y unconditional panel base rate (calibrate.py)
                "dir_accuracy": (sum(r["dir_correct"] for r in dir_rows) / len(dir_rows))
                                if dir_rows else None,
                "n_dir_called": len(dir_rows),
                "sleeve_cum_pnl_pct": sum(r["sleeve_pnl_pct"] for r in pnl_rows)
                                      if pnl_rows else 0.0,
                "n_sleeve_trades": len(pnl_rows),
            },
        }

    @app.get("/api/desk", dependencies=[Depends(_require_token)])
    def desk(days: int = Query(default=60, ge=1, le=365)):
        """Nightly quant-desk review: the gap-to-1%/day time series, the hypothesis ledger and
        the ranked PROPOSALS. Read-only. Proposals are never auto-applied — `applied` is 0 on
        every row the desk writes, and only a human changes that (see docs/DESK_AGENT.md)."""
        from signal_engine.desk.ledger import DeskStore

        store = DeskStore(cfg.env.db_url, read_only=True)
        try:
            reviews = store.fetch_reviews(limit=days)
            gap_gated = store.fetch_gap_series("gated", limit=days)
            gap_trailing = store.fetch_gap_series("trailing", limit=days)
            gap_session = store.fetch_gap_series("session", limit=days)
            hypotheses = store.fetch_hypotheses(limit=200)
            proposals = store.fetch_proposals(limit=200)
            priors = store.priors()
        finally:
            store.close()
        return {
            "latest_review": reviews[0] if reviews else None,
            "reviews": reviews,
            "gap_series": {"session": gap_session, "gated": gap_gated,
                           "trailing": gap_trailing},
            "hypotheses": hypotheses,
            "proposals": proposals,
            "priors": priors,
            "target_daily_net_pct": 1.00,
            "note": ("Paper book, no demonstrated edge. Every proposal is a written diff for a "
                     "human to apply; the desk has no code path that edits config."),
        }

    @app.get("/api/portfolio", dependencies=[Depends(_require_token)])
    def portfolio():
        """The paper portfolio manager: the ₹1,00,000 book's live value, cash, holdings and P&L.
        Simulated money on real prices with the real cost model — decision-support only."""
        return _portfolio_snapshot()

    @app.get("/api/portfolio/equity", dependencies=[Depends(_require_token)])
    def portfolio_equity(days: int = Query(default=30, ge=1, le=365)):
        """The book's equity curve — intraday 'mark' points + daily 'eod' points for `days` back."""
        from signal_engine.storage.repository import SignalRepository

        repo = SignalRepository(cfg.env.db_url, read_only=True)
        try:
            points = repo.fetch_equity_curve(days=days)
        finally:
            repo.close()
        return {"count": len(points), "points": [
            {"ts": p["ts"], "day": p["day"], "kind": p["kind"], "equity": p["equity"],
             "cash": p["cash"], "invested": p["invested"]} for p in points]}

    @app.get("/api/watchlist", dependencies=[Depends(_require_token)])
    def watchlist():
        """The live paper-trading universe — exactly the symbols the live feed subscribes to and
        paper-trades intraday (config/settings.yaml `watchlist`), with each name's sector tag and
        today's activity (trades + net P&L) so the dashboard can show WHAT is being traded and
        which names have fired today. Decision-support only; no orders."""
        import pytz

        syms = list(cfg.settings.watchlist)
        sectors = _watchlist_sectors()
        today = datetime.now(pytz.timezone("Asia/Kolkata")).date().isoformat()

        # Per-symbol activity from the shared paper-trade DB (all-time + today).
        try:
            enriched, _ = _paper_enriched(None, None, None, None)
        except Exception:  # noqa: BLE001 - never let a DB hiccup blank the watchlist
            enriched = []
        today_n: dict = {}
        today_pnl: dict = {}
        total_n: dict = {}
        for t in enriched:
            s = t.get("symbol")
            total_n[s] = total_n.get(s, 0) + 1
            if (t.get("entry_ts") or "")[:10] == today:
                today_n[s] = today_n.get(s, 0) + 1
                today_pnl[s] = today_pnl.get(s, 0.0) + float(t.get("net_pnl_abs") or 0.0)

        # Live open position per symbol (entry/stop/target in ₹ + %), so the watchlist shows the
        # active trade levels — not just "did it trade". Keyed by symbol (one open pos per name).
        try:
            open_positions, _ = _open_positions_view()
        except Exception:  # noqa: BLE001
            open_positions = []
        open_by_sym = {p["symbol"]: p for p in open_positions}

        rows = [{
            "symbol": s,
            "sector": sectors.get(s, ""),
            "trades_today": today_n.get(s, 0),
            "pnl_today": round(today_pnl.get(s, 0.0), 2),
            "trades_total": total_n.get(s, 0),
            "open_position": open_by_sym.get(s),  # null when flat; full levels when in a trade
        } for s in syms]
        return {
            "count": len(syms),
            "date": today,
            "traded_today": sum(1 for r in rows if r["trades_today"] > 0),
            "open_now": len(open_by_sym),
            "symbols": rows,
        }

    @app.websocket("/ws/chart/{symbol}")
    async def ws_chart(ws: WebSocket, symbol: str, date_str: str = None, seed: int = 42,
                       speed: float = 0.0):
        """Stream a session's bars to simulate the live tick->bar push (PLAN §11.6).

        ``speed`` is the delay (seconds) between bars; 0 = as fast as possible (tests).
        Replaces the live Dhan websocket in this offline build.
        """
        await ws.accept()
        d = _parse_date(date_str)
        df = generate_session(symbol, d, seed=seed, regime="trend_up")
        from signal_engine.ingestion.aggregator import BarAggregator
        agg = BarAggregator(symbol, 1)
        try:
            for tick in bars_to_ticks(df, symbol):
                bar = agg.add_tick(tick)
                if bar is not None:
                    await ws.send_json({"time": int(bar.ts.timestamp()), "open": bar.open,
                                        "high": bar.high, "low": bar.low, "close": bar.close})
                    if speed:
                        await asyncio.sleep(speed)
            last = agg.flush()
            if last is not None:
                await ws.send_json({"time": int(last.ts.timestamp()), "open": last.open,
                                    "high": last.high, "low": last.low, "close": last.close})
            await ws.send_json({"done": True})
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/quotes")
    async def ws_quotes(ws: WebSocket, interval: float = 1.0, symbols: str = None):
        """Stream live LTP for the watchlist (or a specific ``symbols`` CSV), every ``interval`` s.

        Backs the dashboard's live-price + sparkline watchlist and the per-stock live chart. All
        connections are fed from a SHARED in-process poller (:class:`_QuoteHub`) — one Dhan REST
        poll covers the union of every connection's symbols, so opening N charts/tabs no longer
        multiplies Dhan load or trips its ~1 req/s rate limit (the old per-connection polling did).
        During market hours these tick; outside hours they hold the last traded price (so the UI
        isn't empty). Needs SE_DATA_SOURCE=dhan + a valid token (refreshed from .env by the hub).
        """
        await ws.accept()
        if cfg.env.data_source != "dhan":
            await ws.send_json({"error": f"live quotes need SE_DATA_SOURCE=dhan (is {cfg.env.data_source!r})"})
            await ws.close()
            return
        if symbols:
            syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:100]
        else:
            syms = []                                   # empty ⇒ full watchlist (resolved by the hub)
        interval = max(0.5, min(float(interval), 5.0))  # clamp the PUSH cadence to a sane 0.5–5s
        cid = quote_hub.subscribe(syms)
        try:
            # Snappy first paint: wait up to ~3s for the shared cache to warm for these symbols
            # (on a cold process the very first poll has to build the broker + scrip master).
            for _ in range(30):
                if quote_hub.slice(syms):
                    break
                await asyncio.sleep(0.1)
            while True:
                data = quote_hub.slice(syms)
                payload = {"ts": quote_hub._ts or int(datetime.now().timestamp()), "quotes": data}
                if not data and quote_hub._last_error:
                    payload["warn"] = quote_hub._last_error
                await ws.send_json(payload)
                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            return
        finally:
            quote_hub.unsubscribe(cid)

    @app.on_event("startup")
    def _prewarm_scans() -> None:
        """Queue the corpus scan for the dashboard's default leaderboard so the first user
        request after a restart isn't the one that waits on it. Non-blocking — this only hands
        work to the scan subprocess. No-op for the synthetic universe (already fast)."""
        if cfg.env.data_source == "mock":
            return
        _leaderboard_cache.prime((True,), scans.archive_leaderboard, True)  # news=True is default
        # Warm the archive load that /api/premarket shares with it, in the same worker.
        _submit_scan(scans.warm_liquid_universe, 150)

    @app.on_event("shutdown")
    def _stop_scans() -> None:
        _shutdown_scan_pool()

    return app


# uvicorn entrypoint: `uvicorn signal_engine.api.app:app`
app = create_app()
