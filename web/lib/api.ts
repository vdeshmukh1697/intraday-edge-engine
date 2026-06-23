// Typed API client for the Signal Engine FastAPI backend.
// Matches the Phase-6 HTTP/WS contracts exactly.

export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"
).replace(/\/$/, "");

export const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface LeaderboardStats {
  universe: number;
  deep_scanned: number;
  filtered_out: number;
  no_signal: number;
  vetoed: number;
  news_vetoed: number;
  candidates: number;
}

export type Direction = "LONG" | "SHORT";

export interface LeaderboardEntry {
  rank: number;
  symbol: string;
  direction: Direction;
  score: number;
  entry: number;
  stop_pct: number;
  t1_pct: number;
  risk_reward: number;
  confidence: number;
  ml_confidence: number | null;
  expected_move_pct: number;
  cost_to_break_even_pct: number;
  sector: string;
  turnover_cr: number;
  reasons: string[];
}

export interface LeaderboardResponse {
  day: string;
  stats: LeaderboardStats;
  entries: LeaderboardEntry[];
}

export interface PremarketOutlook {
  gap_bias: string;
  expected_gap_pct: number;
  risk_tone: string;
  drivers: string[];
}

export interface PremarketPick {
  symbol: string;
  bias: string;
  setup: string;
  expected_gap_pct: number;
  confidence: number;
  catalyst: string;
  drivers: string[];
}

export interface PremarketResponse {
  day: string;
  outlook: PremarketOutlook;
  picks: PremarketPick[];
}

export interface BacktestMetrics {
  trades: number;
  win_rate: number;
  profit_factor: number | null;
  expectancy_pct: number;
  total_net_pct: number;
  max_drawdown_pct: number;
  sharpe: number;
  sortino: number;
  avg_hold_minutes: number;
}

export interface DailyReturn {
  date: string;
  pct: number;
}

export interface StrategyHealth {
  overall: number;
  status: string;
  hit_rate: number;
  profit_factor: number | null;
  expectancy_pct: number;
  calibration_error: number;
  max_drawdown_pct: number;
  components: Record<string, number>;
  window_trades: number;
}

export interface BacktestResponse {
  days: string[];
  picks: unknown;
  metrics: BacktestMetrics;
  equity_curve: number[];
  daily_returns: DailyReturn[];
  health: StrategyHealth;
}

export interface Candle {
  time: number; // epoch seconds
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface LinePoint {
  time: number;
  value: number;
}

export interface ChartResponse {
  symbol: string;
  candles: Candle[];
  overlays: {
    vwap: LinePoint[];
    ema_fast: LinePoint[];
    ema_slow: LinePoint[];
  };
}

// Live WS bar (a candle, or a terminal {done:true} sentinel).
export type LiveBar = Candle;
export interface WsDone {
  done: true;
}
export type WsMessage = LiveBar | WsDone;

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

function headers(): HeadersInit {
  const h: Record<string, string> = { Accept: "application/json" };
  if (API_KEY) h["X-API-Key"] = API_KEY;
  return h;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: headers(),
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      /* ignore */
    }
    throw new Error(
      `API ${res.status} ${res.statusText} for ${path}${
        detail ? `: ${detail.slice(0, 200)}` : ""
      }`
    );
  }
  return (await res.json()) as T;
}

export interface LeaderboardParams {
  date?: string;
  universe?: number;
  top?: number;
  news?: boolean;
  ml?: boolean;
}

export function getLeaderboard(
  params: LeaderboardParams = {}
): Promise<LeaderboardResponse> {
  const q = new URLSearchParams();
  if (params.date) q.set("date", params.date);
  if (params.universe != null) q.set("universe", String(params.universe));
  if (params.top != null) q.set("top", String(params.top));
  if (params.news != null) q.set("news", String(params.news));
  if (params.ml != null) q.set("ml", String(params.ml));
  const qs = q.toString();
  return getJSON<LeaderboardResponse>(`/api/leaderboard${qs ? `?${qs}` : ""}`);
}

export function getPremarket(date?: string): Promise<PremarketResponse> {
  const q = new URLSearchParams();
  if (date) q.set("date", date);
  const qs = q.toString();
  return getJSON<PremarketResponse>(`/api/premarket${qs ? `?${qs}` : ""}`);
}

export interface BacktestParams {
  start?: string;
  days?: number;
}

export function getBacktest(
  params: BacktestParams = {}
): Promise<BacktestResponse> {
  const q = new URLSearchParams();
  if (params.start) q.set("start", params.start);
  if (params.days != null) q.set("days", String(params.days));
  const qs = q.toString();
  return getJSON<BacktestResponse>(`/api/backtest${qs ? `?${qs}` : ""}`);
}

export function getChart(
  symbol: string,
  date?: string
): Promise<ChartResponse> {
  const q = new URLSearchParams();
  if (date) q.set("date", date);
  const qs = q.toString();
  return getJSON<ChartResponse>(
    `/api/chart/${encodeURIComponent(symbol)}${qs ? `?${qs}` : ""}`
  );
}

// Build the WebSocket URL for live chart streaming.
export function chartWsUrl(
  symbol: string,
  dateStr: string,
  speed = 0.2
): string {
  const wsBase = API_BASE.replace(/^http/, "ws");
  const q = new URLSearchParams();
  q.set("date_str", dateStr);
  q.set("speed", String(speed));
  // The engine reads the API key from the X-API-Key header on the HTTP
  // upgrade where possible; browsers cannot set WS headers, so we also pass
  // it as a query param for engines that accept it. Harmless if unused.
  if (API_KEY) q.set("api_key", API_KEY);
  return `${wsBase}/ws/chart/${encodeURIComponent(symbol)}?${q.toString()}`;
}

// Today's date in YYYY-MM-DD (local).
export function todayStr(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}
