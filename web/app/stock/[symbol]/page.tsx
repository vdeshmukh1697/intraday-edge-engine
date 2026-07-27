"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import {
  getChart,
  todayStr,
  getPaperTrades,
  getOpenPositions,
  getWatchlistQuotes,
  type ChartResponse,
  type PaperTrade,
  type OpenPosition,
  type WatchlistQuote,
} from "@/lib/api";
import CandleChart from "@/components/CandleChart";
import LiveChart from "@/components/LiveChart";
import { InfoTip } from "@/components/InfoTip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import {
  Badge,
  EmptyState,
  ErrorBanner,
  PageHeader,
  Section,
} from "@/components/ui/primitives";
import { StatTile } from "@/components/ui/stats";
import { DirectionTag, Money } from "@/components/ui/cells";
import {
  conf,
  countCompact,
  inr,
  inrPrice,
  inrSigned,
  num,
  pctSigned,
  signCls,
} from "@/lib/format";

// Same ~2s latest-tick snapshot the watchlist polls, filtered to this symbol. It carries
// traded volume, which the WS price stream does not.
const QUOTE_MS = 2000;

export default function StockPage() {
  const params = useParams<{ symbol: string }>();
  const search = useSearchParams();
  const symbol = decodeURIComponent(
    Array.isArray(params.symbol) ? params.symbol[0] : params.symbol
  );

  const [date, setDate] = useState<string>(search.get("date") || todayStr());
  const [data, setData] = useState<ChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Paper-trade history + current open position for THIS symbol.
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [openPos, setOpenPos] = useState<OpenPosition | null>(null);
  const [quote, setQuote] = useState<WatchlistQuote | null>(null);

  // Live price + traded volume for this symbol. Best-effort: the chart stands alone.
  useEffect(() => {
    let cancelled = false;
    const pull = () =>
      getWatchlistQuotes()
        .then((q) => {
          if (!cancelled) setQuote(q.symbols.find((s) => s.symbol === symbol) ?? null);
        })
        .catch(() => {
          /* supplementary — never surface it as a page error */
        });
    pull();
    const id = setInterval(pull, QUOTE_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [symbol]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getChart(symbol, date));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [symbol, date]);

  // Paper-trade history + open position for this symbol (independent of the chart date).
  const loadHistory = useCallback(async () => {
    try {
      const [t, o] = await Promise.all([getPaperTrades({ symbol }), getOpenPositions()]);
      setTrades(t.trades);
      setOpenPos(o.positions.find((p) => p.symbol === symbol) || null);
    } catch {
      /* history is best-effort; the chart still renders */
    }
  }, [symbol]);

  useEffect(() => {
    load();
    loadHistory();
    const id = setInterval(loadHistory, 15000); // keep history/open-position fresh
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  return (
    <>
      <PageHeader
        title={symbol}
        eyebrow="Live price · historical session · paper record"
        lede="Streaming price above, the chosen session's candles with VWAP and EMA below, then every paper trade the engine has ever recorded on this name."
        aside={
          openPos ? (
            <Badge tone={openPos.direction === "LONG" ? "positive" : "negative"} size="lg">
              Position open · {openPos.direction}
            </Badge>
          ) : undefined
        }
      />

      {quote && (
        <div className="stat-grid">
          <StatTile
            label="Live ₹"
            value={inrPrice(quote.ltp)}
            tone={signCls(quote.change_pct)}
            sub={
              quote.stale
                ? "market closed or feed stale"
                : `${pctSigned(quote.change_pct)} vs prior close`
            }
          />
          <StatTile
            label={
              <>
                Traded volume
                <InfoTip
                  full="Traded volume"
                  def="Cumulative shares traded in this name so far today, from the live feed."
                />
              </>
            }
            value={countCompact(quote.volume)}
            sub="shares today"
            emphasis="quiet"
          />
        </div>
      )}

      <LiveChart symbol={symbol} />

      {openPos && <OpenPositionCard pos={openPos} />}

      <Section
        title="Historical session"
        note="Candles for the selected date, with the indicator overlays the strategy reads."
        aside={
          <form
            className="row-wrap"
            onSubmit={(e) => {
              e.preventDefault();
              load();
            }}
          >
            <label className="field">
              <span>Date</span>
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </label>
            <button type="submit" disabled={loading}>
              {loading ? "Loading…" : "Load"}
            </button>
          </form>
        }
      >
        <div className="stack-tight">
          <div className="chart-legend">
            <span className="legend-item">
              <span
                className="legend-swatch"
                style={{ background: "var(--series-2)" }}
                aria-hidden="true"
              />
              VWAP
              <InfoTip term="vwap" />
            </span>
            <span className="legend-item">
              <span
                className="legend-swatch"
                style={{ background: "var(--series-1)" }}
                aria-hidden="true"
              />
              EMA fast
              <InfoTip term="ema" />
            </span>
            <span className="legend-item">
              <span
                className="legend-swatch"
                style={{ background: "var(--series-7)" }}
                aria-hidden="true"
              />
              EMA slow (dashed)
            </span>
          </div>

          {error && <ErrorBanner>Failed to load: {error}</ErrorBanner>}

          {data ? (
            <CandleChart
              key={`${symbol}-${date}`}
              candles={data.candles}
              vwap={data.overlays.vwap}
              emaFast={data.overlays.ema_fast}
              emaSlow={data.overlays.ema_slow}
            />
          ) : (
            !loading &&
            !error && (
              <EmptyState title="No chart data for this date">
                Pick a trading session — weekends and holidays have no bars.
              </EmptyState>
            )
          )}
        </div>
      </Section>

      <PaperHistory symbol={symbol} trades={trades} />
    </>
  );
}

// Current open position for this symbol — live entry/target/stop (₹ + %) + unrealized P&L.
function OpenPositionCard({ pos }: { pos: OpenPosition }) {
  return (
    <Section
      titleNode={
        <span className="row-wrap">
          Open position
          <DirectionTag direction={pos.direction} />
        </span>
      }
      note="Paper position, marked to the latest streamed price. No live order exists."
    >
      <div className="stat-grid">
        <StatTile
          label={
            <>
              Unrealized P&amp;L
              <InfoTip term="unrealized_pnl" />
            </>
          }
          value={pctSigned(pos.unrealized_pnl_pct)}
          tone={signCls(pos.unrealized_pnl_pct)}
          sub={pos.unrealized_pnl_abs != null ? inrSigned(pos.unrealized_pnl_abs) : undefined}
        />
        <StatTile
          label={
            <>
              Entry
              <InfoTip term="entry" />
            </>
          }
          value={num(pos.entry)}
          sub={pos.entry_ts ? `since ${pos.entry_ts.slice(11, 16)}` : undefined}
        />
        <StatTile
          label={
            <>
              Last price
              <InfoTip term="ltp" />
            </>
          }
          value={num(pos.last_price)}
        />
        <StatTile
          label={
            <>
              Target
              <InfoTip term="target" />
            </>
          }
          value={num(pos.target)}
          sub={pos.target_pct != null ? pctSigned(pos.target_pct) : undefined}
        />
        <StatTile
          label={
            <>
              Stop
              <InfoTip term="stop" />
            </>
          }
          value={num(pos.stop_loss)}
          sub={pos.stop_pct != null ? `−${pos.stop_pct.toFixed(2)}%` : undefined}
        />
        <StatTile
          label={
            <>
              R:R
              <InfoTip term="rr" />
            </>
          }
          value={num(pos.risk_reward)}
          sub={`rule score ${conf(pos.confidence)} (uncalibrated)`}
        />
      </div>
    </Section>
  );
}

// Full paper-trade history for this symbol.
function PaperHistory({ symbol, trades }: { symbol: string; trades: PaperTrade[] }) {
  const net = trades.reduce((a, t) => a + (t.net_pnl_abs || 0), 0);
  const wins = trades.filter((t) => (t.net_pnl_abs || 0) > 0).length;

  const columns: Column<PaperTrade>[] = [
    {
      id: "entry_ts",
      header: "Entry",
      primary: true,
      cell: (t) => (
        <>
          <span className="mono">{t.entry_ts?.slice(5, 16).replace("T", " ")}</span>
          <span className={`stack-only ${signCls(t.net_pnl_pct)}`}>
            {pctSigned(t.net_pnl_pct)}
          </span>
        </>
      ),
      sortBy: (t) => t.entry_ts,
    },
    {
      id: "dir",
      header: (
        <>
          Dir
          <InfoTip term="direction" />
        </>
      ),
      label: "Direction",
      cell: (t) => <DirectionTag direction={t.direction} />,
    },
    {
      id: "entry_fill",
      header: "Entry ₹",
      label: "Entry price",
      numeric: true,
      cell: (t) => <Money value={t.entry_fill} kind="price" />,
    },
    {
      id: "exit_ts",
      header: "Exit",
      label: "Exit time",
      hideOnStack: true,
      cell: (t) => (
        <span className="mono faint">{t.exit_ts?.slice(5, 16).replace("T", " ")}</span>
      ),
    },
    {
      id: "exit_fill",
      header: "Exit ₹",
      label: "Exit price",
      numeric: true,
      cell: (t) => <Money value={t.exit_fill} kind="price" />,
    },
    {
      id: "reason",
      header: "Reason",
      label: "Exit reason",
      cell: (t) => <span className="chip">{t.exit_reason}</span>,
    },
    {
      id: "target",
      header: (
        <>
          Target
          <InfoTip term="target" />
        </>
      ),
      label: "Target",
      numeric: true,
      hideOnStack: true,
      cell: (t) => <Money value={t.target} kind="price" />,
    },
    {
      id: "stop",
      header: (
        <>
          Stop
          <InfoTip term="stop" />
        </>
      ),
      label: "Stop",
      numeric: true,
      hideOnStack: true,
      cell: (t) => <Money value={t.stop_loss} kind="price" />,
    },
    {
      id: "netpct",
      header: "Net %",
      label: "Net %",
      numeric: true,
      hideOnStack: true,
      cell: (t) => (
        <span className={signCls(t.net_pnl_pct)}>{pctSigned(t.net_pnl_pct)}</span>
      ),
      sortBy: (t) => t.net_pnl_pct,
    },
    {
      id: "netabs",
      header: "Net ₹",
      label: "Net ₹",
      numeric: true,
      cell: (t) => <Money value={t.net_pnl_abs} kind="signed" />,
      sortBy: (t) => t.net_pnl_abs,
    },
    {
      id: "r",
      header: (
        <>
          R<InfoTip term="r_multiple" />
        </>
      ),
      label: "R-multiple",
      numeric: true,
      cell: (t) => (t.r_multiple == null ? "—" : `${num(t.r_multiple)}R`),
    },
  ];

  return (
    <Section
      title={`Paper-trade history — ${symbol} (${trades.length})`}
      aside={
        trades.length > 0 ? (
          <span className={signCls(net)}>
            net <strong>{inr(net)}</strong> · {wins}W/{trades.length - wins}L
          </span>
        ) : undefined
      }
      note="Every paper trade the engine has recorded on this name, newest first."
    >
      <DataTable
        label={`Paper trades for ${symbol}`}
        columns={columns}
        rows={[...trades].reverse()}
        rowKey={(t) => t.id}
        tall
        empty={
          <EmptyState title={`No paper trades on ${symbol} yet`}>
            They appear here as the live paper-trader fires on this name.
          </EmptyState>
        }
      />
    </Section>
  );
}
