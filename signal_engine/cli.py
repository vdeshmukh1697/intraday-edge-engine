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

    broker = build_broker(cfg, day=day, seed=args.seed, regime_map=regime_map)
    if cfg.env.data_source == "dhan":
        print("Live Dhan source selected but it is gated/disabled. Use SE_DATA_SOURCE=mock.")
        return 2

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
    kwargs = dict(seed=args.seed, top_n=args.top)
    if as_of is not None:
        kwargs["as_of"] = as_of
    print(f"Scanning {args.universe}-symbol synthetic NSE universe for {day} "
          f"(as-of {args.as_of or '11:00'})...\n")
    result = run_scan(cfg, universe, day, **kwargs)

    print("=== SCAN STATS ===")
    print(f"universe         : {result.universe_size}")
    print(f"deep-scanned     : {result.deep_scanned}   (passed static liquidity screen)")
    print(f"filtered (cost)  : {result.filtered_out}")
    print(f"no signal        : {result.no_signal}")
    print(f"risk-vetoed      : {result.vetoed}")
    print(f"candidates       : {result.candidates}")

    print(f"\n=== 🏆 BEST INTRADAY SETUPS (top {args.top}) ===")
    if not result.leaderboard:
        print("  (no setups passed the filters/gates for these settings — try --seed)")
    for e in result.leaderboard:
        p = e.plan
        print(
            f"  #{e.rank:<2d} {p.symbol:9s} {p.direction.value:5s} score {e.score:5.1f} "
            f"| entry {p.entry:8.2f} SL -{p.stop_pct:.2f}% T1 +{p.target_pcts[0]:.2f}% "
            f"R:R {p.risk_reward:.2f} conf {p.confidence:.0f} | {e.sector} "
            f"₹{e.turnover_cr:.0f}cr | {', '.join(p.reasons)}"
        )
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
    ps.set_defaults(func=cmd_scan)

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

    pi = sub.add_parser("info", help="Print config + safety summary.")
    pi.set_defaults(func=cmd_info)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
