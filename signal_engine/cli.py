"""Command-line entrypoint.

Examples
--------
    signal-engine replay --date 2025-06-23
    signal-engine replay --date 2025-06-23 --demo   # bias some symbols to trend (shows picks)
    signal-engine info

The ``replay`` command runs a full synthetic trading session through the exact same
pipeline the live engine would use, prints the surfaced picks and the paper-trading
result. No live market, no orders.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from signal_engine.config import load_config
from signal_engine.factory import build_alerter, build_broker
from signal_engine.market.calendar import NSECalendar
from signal_engine.market.session import MarketSession
from signal_engine.strategies.base import create_strategy

_DISCLAIMER = (
    "DISCLAIMER: decision-support only. Not investment advice. No live orders are placed. "
    "Intraday trading carries substantial risk of loss. You alone decide and execute. "
    "(PLAN §9)"
)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def cmd_info(args) -> int:
    cfg = load_config()
    print("Intraday Signal Engine — config summary")
    print(f"  data_source     : {cfg.env.data_source}")
    print(f"  allow_live_orders: {cfg.env.allow_live_orders} (live execution NOT implemented)")
    print(f"  alerter         : {cfg.env.alerter}")
    print(f"  watchlist       : {cfg.settings.watchlist}")
    print(f"  strategy        : {cfg.settings.strategy.active}")
    print(f"  R:R floor       : {cfg.risk.risk.rr_floor}   edge-cost k: {cfg.risk.risk.edge_cost_multiple}")
    print(f"  square-off      : {cfg.settings.market.square_off} IST")
    print(_DISCLAIMER)
    return 0


def cmd_replay(args) -> int:
    cfg = load_config()
    day = _parse_date(args.date) if args.date else date(2025, 6, 23)

    cal = NSECalendar()
    if not cal.is_trading_day(day):
        print(f"{day} is not an NSE trading day (weekend/holiday). Pick another date.")
        return 2

    symbols = args.symbols.split(",") if args.symbols else cfg.settings.watchlist

    # In --demo, bias the first few symbols up / one down so the strategy has setups.
    regime_map = {}
    if args.demo:
        regimes = ["trend_up", "trend_up", "trend_down", "choppy", "choppy"]
        for i, s in enumerate(symbols):
            regime_map[s] = regimes[i % len(regimes)]

    if cfg.env.data_source == "dhan":
        print("`replay` plays a finite synthetic/historical session; Dhan is a live stream. "
              "Use `signal-engine live` for the Dhan feed, or set SE_DATA_SOURCE=mock to replay.")
        return 2
    broker = build_broker(cfg, day=day, seed=args.seed, regime_map=regime_map)

    strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
    session = MarketSession(cfg.settings.market, cal)
    alerter = build_alerter(cfg)

    # Lazy import to keep CLI import light.
    from signal_engine.engine.runner import EngineRunner

    repo = None
    if args.persist:
        from signal_engine.storage.repository import SignalRepository

        repo = SignalRepository(cfg.env.db_url)

    runner = EngineRunner(cfg, broker, strategy, session, alerter, repo=repo)
    print(f"Replaying {day} for {symbols} (source={cfg.env.data_source}, seed={args.seed})\n")
    summary = runner.replay(symbols)

    print("\n=== RESULT ===")
    print(f"bars processed : {summary.bars_processed}")
    print(f"picks surfaced : {len(summary.picks)}")
    print(f"paper trades   : {len(summary.closed)}")
    print(f"win rate       : {summary.win_rate}%")
    print(f"net P&L (sum %): {summary.net_pnl_pct:+.2f}%  (capital-agnostic, net of costs)")
    if summary.picks:
        print("\nTop picks (by confidence):")
        for p in sorted(summary.picks, key=lambda x: x.confidence, reverse=True)[:10]:
            print(
                f"  {p.symbol:10s} {p.direction.value:5s} entry {p.entry:.2f} "
                f"SL -{p.stop_pct:.2f}% T1 +{p.target_pcts[0]:.2f}% R:R {p.risk_reward:.2f} "
                f"conf {p.confidence:.0f}  [{', '.join(p.reasons)}]"
            )
    if repo:
        repo.close()
    print("\n" + _DISCLAIMER)
    return 0


def cmd_scan(args) -> int:
    """Full-universe scan -> ranked 'best intraday stocks' leaderboard (Phase 2)."""
    cfg = load_config()
    day = _parse_date(args.date) if args.date else date(2025, 6, 23)

    cal = NSECalendar()
    if not cal.is_trading_day(day):
        print(f"{day} is not an NSE trading day. Pick another date.")
        return 2

    as_of = datetime.strptime(args.as_of, "%H:%M").time() if args.as_of else None

    from signal_engine.scan.harness import run_scan
    from signal_engine.universe.mock import MockUniverseProvider

    universe = MockUniverseProvider(n=args.universe, seed=args.seed)
    kwargs = dict(seed=args.seed, top_n=args.top, with_news=not args.no_news, with_ml=args.ml)
    if as_of is not None:
        kwargs["as_of"] = as_of
    print(f"Scanning {args.universe}-symbol synthetic NSE universe for {day} "
          f"(as-of {args.as_of or '11:00'}, news={'off' if args.no_news else 'on'}, "
          f"ml={'shadow' if args.ml else 'off'})...\n")
    result = run_scan(cfg, universe, day, **kwargs)

    print("=== SCAN STATS ===")
    print(f"universe         : {result.universe_size}")
    print(f"deep-scanned     : {result.deep_scanned}   (passed static liquidity screen)")
    print(f"filtered (cost)  : {result.filtered_out}")
    print(f"no signal        : {result.no_signal}")
    print(f"risk-vetoed      : {result.vetoed}")
    print(f"news-vetoed/guard: {result.news_vetoed}")
    print(f"candidates       : {result.candidates}")

    print(f"\n=== 🏆 BEST INTRADAY SETUPS (top {args.top}) ===")
    if not result.leaderboard:
        print("  (no setups passed the filters/gates for these settings — try --seed)")
    for e in result.leaderboard:
        p = e.plan
        ml = result.ml_confidence.get(p.symbol)
        ml_str = f" ML {ml:.0f}" if ml is not None else ""
        print(
            f"  #{e.rank:<2d} {p.symbol:9s} {p.direction.value:5s} score {e.score:5.1f} "
            f"| entry {p.entry:8.2f} SL -{p.stop_pct:.2f}% T1 +{p.target_pcts[0]:.2f}% "
            f"R:R {p.risk_reward:.2f} conf {p.confidence:.0f}{ml_str} | {e.sector} "
            f"₹{e.turnover_cr:.0f}cr | {', '.join(p.reasons)}"
        )
    if args.ml and not result.ml_confidence:
        print("  (no trained model found — run `train` first to enable shadow ML)")
    print("\n" + _DISCLAIMER)
    return 0


def _run_backtest(args):
    cfg = load_config()
    start = _parse_date(args.start) if args.start else date(2025, 6, 2)
    symbols = args.symbols.split(",") if args.symbols else cfg.settings.watchlist
    from signal_engine.backtest.engine import run_backtest

    print(f"Backtesting {symbols} over {args.days} trading days from {start} (seed={args.seed})...\n")
    return cfg, run_backtest(cfg, symbols, start, args.days, seed=args.seed)


def cmd_backtest(args) -> int:
    """Multi-day event-driven backtest over the shared core (PLAN §6)."""
    cfg, res = _run_backtest(args)
    m = res.metrics
    pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
    print("=== BACKTEST METRICS (net of costs) ===")
    print(f"days            : {len(res.days)}   picks: {res.picks}   trades: {m.trades}")
    print(f"win rate        : {m.win_rate:.1f}%   (W {m.wins} / L {m.losses})")
    print(f"profit factor   : {pf}")
    print(f"expectancy/trade: {m.expectancy_pct:+.3f}%   total net: {m.total_net_pct:+.2f}%")
    print(f"avg win / loss  : {m.avg_win_pct:+.3f}% / {m.avg_loss_pct:+.3f}%")
    print(f"max drawdown    : {m.max_drawdown_pct:.2f}%  ({m.max_drawdown_days} day(s))")
    print(f"Sharpe / Sortino: {m.sharpe:.2f} / {m.sortino:.2f}   avg hold: {m.avg_hold_minutes:.0f} min")
    print(f"\nStrategy Health : {res.health.overall:.0f}/100 [{res.health.status.upper()}]")
    print("\n" + _DISCLAIMER)
    return 0


def cmd_health(args) -> int:
    """Backtest, then show the Strategy Health breakdown + fire a degradation alert if unhealthy."""
    cfg, res = _run_backtest(args)
    h = res.health
    print("=== STRATEGY HEALTH (PLAN §6.6) ===")
    print(f"overall         : {h.overall:.0f}/100  [{h.status.upper()}]   (window {h.window_trades} trades)")
    print(f"hit rate        : {h.hit_rate:.1f}%")
    pf = "inf" if h.profit_factor == float("inf") else f"{h.profit_factor:.2f}"
    print(f"profit factor   : {pf}")
    print(f"expectancy      : {h.expectancy_pct:+.3f}%")
    print(f"calibration err : {h.calibration_error:.3f} (Brier; lower=better)")
    print(f"max drawdown    : {h.max_drawdown_pct:.2f}%")
    print("components (0-1): " + ", ".join(f"{k}={v:.2f}" for k, v in h.components.items()))

    from signal_engine.health.scorer import detect_degradation

    alert = detect_degradation(h, threshold=args.threshold)
    if alert:
        from signal_engine.factory import build_alerter

        build_alerter(cfg).send(alert, level="alert")
        print(f"\n⚠️  DEGRADATION ALERT FIRED: {alert}")
    else:
        print(f"\n✓ Health above threshold ({args.threshold}); no alert.")
    print("\n" + _DISCLAIMER)
    return 0


def cmd_news(args) -> int:
    """Preview the synthetic news headlines for a day (mapped + scored)."""
    cfg = load_config()
    day = _parse_date(args.date) if args.date else date(2025, 6, 23)
    symbols = args.symbols.split(",") if args.symbols else cfg.settings.watchlist
    from signal_engine.news.provider import MockNewsProvider

    items = MockNewsProvider(symbols, day, seed=args.seed).fetch()
    print(f"Synthetic news for {day} ({len(items)} items):\n")
    for it in items:
        print(f"  {it.ts.strftime('%H:%M')}  {','.join(it.symbols):10s} "
              f"sent {it.sentiment:+.2f}  {it.event_type.value:11s}  {it.headline}")
    if not items:
        print("  (no items generated for these symbols/seed)")
    print("\n" + _DISCLAIMER)
    return 0


def cmd_premarket(args) -> int:
    """Pre-market briefing: overnight cues + news -> ranked gap/bias watchlist (PLAN §4.8)."""
    cfg = load_config()
    day = _parse_date(args.date) if args.date else date(2025, 6, 23)
    symbols = args.symbols.split(",") if args.symbols else cfg.settings.watchlist

    cal = NSECalendar()
    if not cal.is_trading_day(day):
        print(f"{day} is not an NSE trading day. Pick another date.")
        return 2

    from signal_engine.factory import build_cues_provider
    from signal_engine.premarket.briefing import build_briefing

    cues = build_cues_provider(cfg)  # real Yahoo cues if SE_CUES_SOURCE=yahoo, else mock
    if cues is not None:
        print("(using live Yahoo Finance global cues)")
    b = build_briefing(cfg, symbols=symbols, day=day, seed=args.seed, top_n=args.top,
                       cues_provider=cues)
    o = b.index_outlook
    print(f"=== PRE-MARKET BRIEFING — {day} ===")
    print(f"Index outlook   : {o.gap_bias.value}  expected gap {o.expected_gap_pct:+.2f}%  "
          f"tone {o.risk_tone.value}")
    print(f"  drivers       : {', '.join(o.drivers)}")
    print(f"\nPre-open watchlist (top {args.top}):")
    if not b.picks:
        print("  (no actionable pre-open bias for these settings)")
    for i, p in enumerate(b.picks, 1):
        print(f"  {i:>2d}. {p.symbol:10s} {p.bias.value:5s} {p.setup:16s} "
              f"gap~{p.expected_gap_pct:+.2f}% conf {p.confidence:.0f}  "
              f"| {p.catalyst}  [{', '.join(p.drivers)}]")

    # Deliver the ~09:00 briefing via the configured alerter (Telegram/WhatsApp/console).
    if args.alert:
        from signal_engine.factory import build_alerter

        top = b.picks[0] if b.picks else None
        msg = (f"Pre-market {day}: {o.gap_bias.value} ({o.expected_gap_pct:+.2f}%), "
               f"{o.risk_tone.value}. Top pick: "
               + (f"{top.symbol} {top.bias.value} ({top.setup}, conf {top.confidence:.0f})"
                  if top else "none"))
        build_alerter(cfg).send(msg, level="signal")
        print("\n(briefing sent via alerter)")
    print("\n" + _DISCLAIMER)
    return 0


def cmd_train(args) -> int:
    """Train the ML signal scorer on labeled synthetic trades; compare vs the rules baseline."""
    cfg = load_config()
    start = _parse_date(args.start) if args.start else date(2025, 6, 2)

    if getattr(args, "source", "synthetic") == "archive":
        # Train on the REAL backfilled 5-year corpus (the most-liquid archived names).
        from signal_engine.ml.train import train_model_from_archive
        from signal_engine.obs.logging_setup import get_logger
        from signal_engine.storage.bars import ParquetBarStore

        store = ParquetBarStore(cfg.env.parquet_dir)
        if args.symbols:
            syms = args.symbols.split(",")
        else:
            # Default to the most-liquid archived names (by latest-session turnover).
            ranked = []
            for s in store.list_symbols():
                d = store.load_latest_session(s)
                if d is not None and not d.empty:
                    ranked.append((float((d["close"] * d["volume"]).sum()), s))
            ranked.sort(reverse=True)
            syms = [s for _, s in ranked[: args.max_symbols]]
        print(f"Training on REAL archive: {len(syms)} symbols, stride={args.stride}, "
              f"max_samples={args.max_samples}...\n")
        model, rep = train_model_from_archive(
            cfg, store, syms, stride=args.stride, max_samples=args.max_samples,
            test_frac=args.test_frac, model_path=args.out, log=get_logger("train"))
    else:
        symbols = args.symbols.split(",") if args.symbols else cfg.settings.watchlist
        from signal_engine.ml.train import train_model

        print(f"Building dataset + training over {args.days} days from {start} "
              f"({len(symbols)} symbols, seed={args.seed})...\n")
        model, rep = train_model(cfg, symbols, start, args.days, seed=args.seed,
                                 test_frac=args.test_frac, model_path=args.out)
    if model is None:
        print(f"Too few samples ({rep.n_samples}); widen --days/--symbols.")
        return 2
    print("=== TRAINING REPORT (LightGBM optional; numpy logistic default) ===")
    print(f"samples        : {rep.n_samples}  (train {rep.n_train} / test {rep.n_test})")
    print(f"base rate (won): {rep.base_rate:.3f}")
    print(f"ML    out-of-sample: acc {rep.ml['accuracy']:.3f}  AUC {rep.ml['auc']:.3f}  brier {rep.ml['brier']:.3f}")
    print(f"rules out-of-sample: acc {rep.rules['accuracy']:.3f}  AUC {rep.rules['auc']:.3f}  brier {rep.rules['brier']:.3f}")
    verdict = "ML BEATS rules" if (rep.auc_gain > 0 and rep.brier_gain > 0) else "ML does NOT clearly beat rules"
    print(f"AUC gain {rep.auc_gain:+.3f}  Brier gain {rep.brier_gain:+.3f}  ->  {verdict}")
    print("top features   : " + ", ".join(
        f"{k} {v:.2f}" for k, v in sorted(rep.importances.items(), key=lambda kv: -kv[1])[:5]))
    print(f"\nModel saved to {rep.model_path}.  Use `scan --ml` to score in SHADOW mode.")
    print("Note: ML stays shadow-only until it beats rules out-of-sample AND in forward "
          "paper-trading (PLAN §4.7/§8). It does not change live decisions.")
    print("\n" + _DISCLAIMER)
    return 0


def _run_scheduler() -> int:
    try:
        from signal_engine.scheduler import start
    except ImportError:
        print("Scheduler deps not installed. Run: pip install apscheduler")
        return 2
    print("Starting daily scheduler (pre-market 08:30 / scan 09-14 / archive 16:00 IST). Ctrl-C to stop.")
    print(_DISCLAIMER)
    start()
    return 0


def cmd_live(args) -> int:
    """Stream the live Dhan feed through the pipeline until market close (paper only)."""
    cfg = load_config()
    if cfg.env.data_source != "dhan":
        print(f"`live` needs a streaming source. Set SE_DATA_SOURCE=dhan "
              f"(currently {cfg.env.data_source!r}).")
        return 2

    cal = NSECalendar()
    from datetime import datetime

    import pytz
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    if not cal.is_trading_day(now.date()):
        print(f"{now.date()} is not an NSE trading day (weekend/holiday). Nothing to stream.")
        return 2

    symbols = args.symbols.split(",") if args.symbols else cfg.settings.watchlist
    broker = build_broker(cfg, day=now.date())  # loads Dhan instrument master + token
    strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
    session = MarketSession(cfg.settings.market, cal)
    alerter = build_alerter(cfg)

    from signal_engine.engine.runner import EngineRunner

    repo = None
    if args.persist:
        from signal_engine.storage.repository import SignalRepository

        repo = SignalRepository(cfg.env.db_url)

    runner = EngineRunner(cfg, broker, strategy, session, alerter, repo=repo)
    print(f"LIVE Dhan feed for {len(symbols)} symbols — streaming until market close.")
    print(_DISCLAIMER)
    summary = runner.live(symbols)

    print("\n=== SESSION RESULT ===")
    print(f"bars processed : {summary.bars_processed}")
    print(f"picks surfaced : {len(summary.picks)}")
    print(f"paper trades   : {len(summary.closed)}")
    if repo:
        repo.close()
    return 0


def cmd_renew_token(args) -> int:
    """Rotate the Dhan 24h access token (RenewToken) and persist it to .env."""
    cfg = load_config()
    if not (cfg.env.dhan_client_id and cfg.env.dhan_access_token):
        print("No Dhan client_id/token in .env to renew.")
        return 2
    from signal_engine.brokers.dhan import token_expiry
    from signal_engine.brokers.dhan_auth import renew_token, update_env_token

    try:
        new = renew_token(cfg.env.dhan_client_id, cfg.env.dhan_access_token)
    except Exception as exc:  # noqa: BLE001
        print(f"Renew failed: {exc}\n(If the token already expired, regenerate it once in "
              f"the Dhan portal — RenewToken only works on an active token.)")
        return 1
    update_env_token(new)
    print(f"Token renewed and written to .env (previous saved to .env.bak). "
          f"New expiry (UTC): {token_expiry(new)}")
    return 0


def cmd_backfill(args) -> int:
    """Bulk-download N years of 1-min bars for the NSE universe from Dhan -> Parquet."""
    cfg = load_config()
    if cfg.env.data_source != "dhan":
        print(f"`backfill` needs Dhan. Set SE_DATA_SOURCE=dhan (currently {cfg.env.data_source!r}).")
        return 2

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime

    import pytz

    from signal_engine.data.backfill import backfill_symbol
    from signal_engine.storage.bars import ParquetBarStore
    from signal_engine.universe.nse import load_nse_equity_symbols

    broker = build_broker(cfg, day=datetime.now(pytz.timezone("Asia/Kolkata")).date())
    # Backfill the official NSE-EQ list, restricted to names Dhan can address (has a security_id).
    eq = load_nse_equity_symbols()
    symbols = [s for s in eq if broker.instruments.ref(s) is not None]
    if args.limit:
        symbols = symbols[: args.limit]
    store = ParquetBarStore(cfg.env.parquet_dir)
    print(f"Backfilling {args.years}y of 1m bars for {len(symbols)} symbols "
          f"({args.workers} workers) -> {cfg.env.parquet_dir}")

    done = total_bars = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(backfill_symbol, broker, store, s, years=args.years): s
                for s in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                total_bars += fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  {sym}: failed ({exc})")
            done += 1
            if done % 25 == 0 or done == len(symbols):
                print(f"  {done}/{len(symbols)} symbols, {total_bars:,} bars saved")
    print(f"Backfill complete: {total_bars:,} bars across {len(symbols)} symbols.")
    return 0


def cmd_serve(args) -> int:
    """Run the FastAPI engine API (the backend the Next.js/Vercel dashboard consumes)."""
    try:
        import uvicorn
    except ImportError:
        print("API deps not installed. Run: pip install fastapi 'uvicorn[standard]' httpx")
        return 2
    print(f"Serving engine API on http://{args.host}:{args.port}  (docs at /docs)")
    print(_DISCLAIMER)
    uvicorn.run("signal_engine.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="signal-engine", description="Intraday signal engine (decision-support only).")
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("replay", help="Replay a synthetic/historical session through the pipeline.")
    pr.add_argument("--date", help="Trading day YYYY-MM-DD (default 2025-06-23).")
    pr.add_argument("--symbols", help="Comma-separated symbols (default from config watchlist).")
    pr.add_argument("--seed", type=int, default=42, help="Synthetic data seed.")
    pr.add_argument("--demo", action="store_true", help="Bias regimes so setups appear.")
    pr.add_argument("--persist", action="store_true", help="Write plans/trades to SQLite.")
    pr.set_defaults(func=cmd_replay)

    ps = sub.add_parser("scan", help="Full-universe scan -> ranked best-intraday leaderboard.")
    ps.add_argument("--date", help="Trading day YYYY-MM-DD (default 2025-06-23).")
    ps.add_argument("--as-of", help="Snapshot time HH:MM IST (default 11:00).")
    ps.add_argument("--universe", type=int, default=2000, help="Synthetic universe size.")
    ps.add_argument("--seed", type=int, default=42, help="Synthetic data seed.")
    ps.add_argument("--top", type=int, default=20, help="Leaderboard size (Top-N).")
    ps.add_argument("--no-news", action="store_true", help="Disable the news overlay.")
    ps.add_argument("--ml", action="store_true", help="Show shadow ML confidence (needs a trained model).")
    ps.set_defaults(func=cmd_scan)

    pt = sub.add_parser("train", help="Train the ML signal scorer; compare vs the rules baseline.")
    pt.add_argument("--start", help="Start date YYYY-MM-DD (default 2025-06-02).")
    pt.add_argument("--days", type=int, default=20, help="Trading days of data to build.")
    pt.add_argument("--symbols", help="Comma-separated symbols (default config watchlist).")
    pt.add_argument("--seed", type=int, default=42, help="Synthetic data seed.")
    pt.add_argument("--test-frac", type=float, default=0.3, help="Out-of-sample fraction.")
    pt.add_argument("--out", default="data/models/signal_model.json", help="Model output path.")
    pt.add_argument("--source", choices=["synthetic", "archive"], default="synthetic",
                    help="'archive' trains on the real backfilled 5-year corpus.")
    pt.add_argument("--stride", type=int, default=2, help="Bar stride for archive training.")
    pt.add_argument("--max-symbols", type=int, default=400,
                    help="Archive: train on the N most-liquid names (default 400).")
    pt.add_argument("--max-samples", type=int, default=300000,
                    help="Cap labeled samples (bounds an archive run; keep memory-safe).")
    pt.set_defaults(func=cmd_train)

    pn = sub.add_parser("news", help="Preview the day's (synthetic) news headlines.")
    pn.add_argument("--date", help="Trading day YYYY-MM-DD (default 2025-06-23).")
    pn.add_argument("--symbols", help="Comma-separated symbols (default config watchlist).")
    pn.add_argument("--seed", type=int, default=42, help="Synthetic news seed.")
    pn.set_defaults(func=cmd_news)

    pm = sub.add_parser("premarket", help="Pre-market briefing -> ranked gap/bias watchlist.")
    pm.add_argument("--date", help="Trading day YYYY-MM-DD (default 2025-06-23).")
    pm.add_argument("--symbols", help="Comma-separated symbols (default config watchlist).")
    pm.add_argument("--seed", type=int, default=42, help="Synthetic data seed.")
    pm.add_argument("--top", type=int, default=20, help="Watchlist size.")
    pm.add_argument("--alert", action="store_true", help="Send the briefing via the configured alerter.")
    pm.set_defaults(func=cmd_premarket)

    pb = sub.add_parser("backtest", help="Multi-day event-driven backtest + metrics.")
    pb.add_argument("--start", help="Start date YYYY-MM-DD (default 2025-06-02).")
    pb.add_argument("--days", type=int, default=10, help="Number of trading days.")
    pb.add_argument("--symbols", help="Comma-separated symbols (default config watchlist).")
    pb.add_argument("--seed", type=int, default=42, help="Synthetic data seed.")
    pb.set_defaults(func=cmd_backtest)

    ph = sub.add_parser("health", help="Backtest then show Strategy Health + degradation alert.")
    ph.add_argument("--start", help="Start date YYYY-MM-DD (default 2025-06-02).")
    ph.add_argument("--days", type=int, default=10, help="Number of trading days.")
    ph.add_argument("--symbols", help="Comma-separated symbols (default config watchlist).")
    ph.add_argument("--seed", type=int, default=42, help="Synthetic data seed.")
    ph.add_argument("--threshold", type=float, default=50.0, help="Health alert threshold.")
    ph.set_defaults(func=cmd_health)

    psch = sub.add_parser("schedule", help="Run the daily job scheduler (pre-market/scan/archive).")
    psch.set_defaults(func=lambda args: (_run_scheduler()))

    pl = sub.add_parser("live", help="Stream the live Dhan feed through the pipeline (paper only).")
    pl.add_argument("--symbols", help="Comma-separated symbols (default from config watchlist).")
    pl.add_argument("--persist", action="store_true", help="Persist plans/positions to the DB.")
    pl.set_defaults(func=cmd_live)

    prt = sub.add_parser("renew-token", help="Rotate the Dhan 24h access token and save to .env.")
    prt.set_defaults(func=cmd_renew_token)

    pbf = sub.add_parser("backfill", help="Bulk-download N years of 1m bars (Dhan) to Parquet.")
    pbf.add_argument("--years", type=int, default=5, help="Years of history (default 5).")
    pbf.add_argument("--limit", type=int, default=None, help="Cap number of symbols (testing).")
    pbf.add_argument("--workers", type=int, default=4, help="Concurrent fetchers (<=5 req/s cap).")
    pbf.set_defaults(func=cmd_backfill)

    pv = sub.add_parser("serve", help="Run the FastAPI engine API for the dashboard.")
    pv.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    pv.add_argument("--port", type=int, default=8000, help="Bind port (default 8000).")
    pv.add_argument("--reload", action="store_true", help="Auto-reload (dev).")
    pv.set_defaults(func=cmd_serve)

    pi = sub.add_parser("info", help="Print config + safety summary.")
    pi.set_defaults(func=cmd_info)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
