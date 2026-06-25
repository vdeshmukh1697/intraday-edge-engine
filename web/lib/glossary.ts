// Central glossary of every term/abbreviation shown on the dashboard, with full forms and
// plain-English definitions. Keyed by a short id used by <InfoTip term="..."/>. Keeping the
// definitions in one place keeps them consistent across pages and easy to maintain.

export interface GlossaryEntry {
  /** Full form / expanded name, e.g. "Profit Factor". */
  full: string;
  /** Plain-English explanation (one or two sentences). */
  def: string;
}

export const GLOSSARY: Record<string, GlossaryEntry> = {
  // ---- Paper-trading performance ----
  net_pnl: { full: "Net Profit & Loss", def: "Total realized profit/loss after costs (brokerage, taxes, slippage) across all trades, in ₹ at a fixed reference notional." },
  win_rate: { full: "Win Rate", def: "Share of trades that ended profitable. 60% = 6 of every 10 trades made money. High win rate alone doesn't guarantee profit — size of wins vs losses matters too." },
  profit_factor: { full: "Profit Factor (PF)", def: "Gross profit ÷ gross loss. Above 1.0 = the strategy makes money overall; below 1.0 = it loses. 2.0 means winners brought in twice what losers gave back." },
  expectancy: { full: "Expectancy", def: "Average ₹ you'd expect to make (or lose) per trade over the long run = (win% × avg win) − (loss% × avg loss)." },
  max_drawdown: { full: "Maximum Drawdown", def: "The largest peak-to-trough drop in cumulative P&L — the worst losing streak you'd have sat through. Lower is better." },
  avg_win_loss: { full: "Average Win / Average Loss", def: "Mean ₹ gained on winning trades vs mean ₹ lost on losing trades." },
  best_worst: { full: "Best / Worst Trade", def: "The single most profitable and most loss-making trade in the selected period." },
  equity_curve: { full: "Equity Curve", def: "Running total of net P&L over time. A rising line = the book is growing; dips are drawdowns." },
  drawdown_series: { full: "Drawdown", def: "How far below its all-time peak the equity curve is at each point. It touches 0 at new highs and goes negative during losing stretches." },
  pnl_distribution: { full: "P&L Distribution", def: "Histogram of per-trade results — how many trades landed in each profit/loss bucket. Shows whether results cluster small or have fat tails." },
  by_strategy: { full: "By Strategy", def: "Performance broken down per trading strategy, so you can see which logic is contributing." },
  by_symbol: { full: "By Symbol", def: "Performance broken down per stock, so you can see which names help or hurt." },
  by_tod: { full: "By Time of Day", def: "Performance grouped by the time the trade was entered — surfaces whether the open, midday, or close is where edge (or losses) come from." },

  // ---- Open positions / trade levels ----
  entry: { full: "Entry Price", def: "The price (₹ per share) at which the paper position was opened." },
  ltp: { full: "Last Traded Price (LTP)", def: "The most recent market price for the stock — used to mark the open position to market." },
  target: { full: "Target (T1)", def: "The planned exit price to take profit, shown as price (₹) and the % move from entry needed to reach it." },
  stop: { full: "Stop-Loss (SL)", def: "The protective exit price that caps the loss, shown as price (₹) and the % move from entry at which it triggers." },
  unrealized_pnl: { full: "Unrealized P&L", def: "Profit/loss on a still-open position if it were closed at the current price — it isn't locked in until the position exits." },
  direction: { full: "Direction", def: "LONG = betting the price rises (buy then sell). SHORT = betting it falls (sell then buy back)." },
  rr: { full: "Risk : Reward (R:R)", def: "How much you aim to make versus risk. 2.0 means the target is twice as far as the stop — risk ₹1 to make ₹2." },
  r_multiple: { full: "R-multiple", def: "Result of a trade in units of the risk taken. +2R = made twice the amount risked; −1R = lost the full planned risk." },
  confidence: { full: "Confidence", def: "The strategy's conviction in the setup (0–100), from rule strength — NOT a probability of profit and never a win-rate guarantee." },
  qty: { full: "Quantity", def: "Number of shares sized for the trade, from the per-trade risk and the stop distance." },

  // ---- Exit reasons ----
  exit_target: { full: "Exit: TARGET", def: "The position closed because price reached the profit target." },
  exit_stop: { full: "Exit: STOP", def: "The position closed because price hit the stop-loss." },
  exit_time: { full: "Exit: TIME_STOP", def: "The position closed because it was held to a maximum time without hitting target or stop." },
  exit_squareoff: { full: "Exit: SQUARE_OFF", def: "The position was force-closed at the intraday square-off time (no overnight holds)." },
  exit_reversal: { full: "Exit: REVERSAL", def: "The position closed because the signal flipped to the opposite direction." },

  // ---- Indicators / signal reasons ----
  vwap: { full: "Volume-Weighted Average Price (VWAP)", def: "The average price weighted by volume since the open — a fair-value line intraday traders watch. Price above VWAP = bullish lean, below = bearish." },
  ema: { full: "Exponential Moving Average (EMA)", def: "A moving average that weights recent prices more. 'EMA fast > slow' signals upward momentum; the reverse signals downward." },
  adx: { full: "Average Directional Index (ADX)", def: "Measures trend STRENGTH (0–100), not direction. Higher = a stronger trend; low ADX = choppy/sideways. This strategy only trades when ADX is high enough." },
  rsi: { full: "Relative Strength Index (RSI)", def: "Momentum oscillator (0–100). Above ~70 is often 'overbought', below ~30 'oversold'." },
  rvol: { full: "Relative Volume (RVOL)", def: "Current volume vs the typical volume for this time of day. RVOL > 1 means unusually active — confirmation that a move has participation." },
  atr: { full: "Average True Range (ATR)", def: "Average size of a bar's price range — a volatility gauge used to place stops/targets proportional to how much the stock moves." },
  orb: { full: "Opening Range Breakout (ORB)", def: "The high/low of the first few minutes after the open; a break beyond it is a common intraday trigger." },

  // ---- Leaderboard / scan ----
  universe: { full: "Universe", def: "The total number of stocks considered before any filtering." },
  deep_scanned: { full: "Deep-scanned", def: "Names that passed the cheap liquidity pre-screen and got the full indicator/strategy analysis." },
  filtered_out: { full: "Filtered out", def: "Names dropped by liquidity/cost filters (too illiquid, too cheap, or costs would eat the edge)." },
  no_signal: { full: "No signal", def: "Scanned names where the strategy found no qualifying setup." },
  vetoed: { full: "Risk-vetoed", def: "Setups rejected by the risk layer (e.g. reward-to-risk too low, or cost exceeds expected edge)." },
  news_vetoed: { full: "News-vetoed", def: "Setups suppressed because of an adverse or event-risk news flag." },
  candidates: { full: "Candidates", def: "Tradeable setups that survived every filter — the leaderboard ranks these." },
  score: { full: "Score", def: "Composite ranking score = confidence × reward:risk × liquidity × catalyst, penalized by cost. Higher ranks higher." },
  ml_conf: { full: "ML Confidence (shadow)", def: "A machine-learning model's score for the setup, shown alongside the rules but NOT changing decisions (shadow mode) until it's proven out-of-sample." },
  expected_move: { full: "Expected Move", def: "The size of move (%) the setup anticipates, from volatility (ATR) and structure." },
  cost_to_break_even: { full: "Cost to Break-even", def: "The % move needed just to cover round-trip costs (brokerage, taxes, slippage) before any profit." },
  turnover: { full: "Turnover", def: "Rupee value traded (₹ crore) — a liquidity measure. Higher turnover = easier to enter/exit without moving the price." },
  sector: { full: "Sector", def: "The stock's industry/sector classification (e.g. private bank, IT, metal). Used to keep the basket diversified." },

  // ---- Pre-market ----
  gap_bias: { full: "Gap Bias", def: "The expected direction of the opening gap for the index — GAP_UP, GAP_DOWN, or FLAT — from overnight global cues." },
  expected_gap: { full: "Expected Gap", def: "The estimated size (%) the index will open above/below yesterday's close." },
  risk_tone: { full: "Risk Tone", def: "Overall market mood from global cues — RISK_ON (appetite for risk, supportive) vs RISK_OFF (defensive)." },
  gift: { full: "GIFT Nifty", def: "Nifty futures traded at GIFT City — a key pre-open tell for how India's index is likely to open." },
  adr: { full: "American Depositary Receipt (ADR)", def: "An Indian company's shares traded in the US overnight. Their overnight move hints at how the stock may open here. Only ~15–30 large names have ADRs." },
  bias: { full: "Bias", def: "The pre-open directional lean for a stock: LONG (expected to rise) or SHORT (expected to fall)." },
  setup: { full: "Setup", def: "The pattern behind the pick — e.g. 'momentum' (continuation), 'reversal' (fade), or 'gap-down momentum'." },
  catalyst: { full: "Catalyst", def: "The news event driving the bias — EARNINGS, CORP_ACTION (dividend/buyback/split), LITIGATION (legal/regulatory), UPGRADE/DOWNGRADE (analyst rating)." },
  drivers: { full: "Drivers", def: "The individual inputs to the bias score: news sentiment, the ADR's overnight move, the index gap, and the prior day's return." },

  // ---- Backtest / health ----
  sharpe: { full: "Sharpe Ratio", def: "Return earned per unit of total volatility — higher means smoother, more efficient returns." },
  sortino: { full: "Sortino Ratio", def: "Like Sharpe but only penalizes DOWNSIDE volatility — rewards strategies whose swings are mostly upward." },
  health_score: { full: "Strategy Health Score", def: "A composite 0–100 of hit rate, profit factor, expectancy, calibration, and drawdown — a quick gauge of whether the strategy is holding up." },
  calibration: { full: "Calibration (Brier)", def: "How well the stated confidence matches actual outcomes (lower Brier = better calibrated). High confidence that doesn't win flags overconfidence." },
  avg_hold: { full: "Average Hold", def: "Mean time a position stays open before exiting." },

  // ---- Feed / status ----
  feed_status: { full: "Feed Status", def: "Whether the live market-data feed is connected and processing bars. 'Live' with a recent timestamp = healthy; 'stale' = no update recently (market closed or feed down)." },
  notional: { full: "Reference Notional", def: "A fixed ₹ position value used to convert % returns into ₹ figures, since the tool is capital-agnostic (you choose your real size)." },
};

export function lookup(term: string): GlossaryEntry | undefined {
  return GLOSSARY[term];
}
