"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { InfoTip } from "@/components/InfoTip";
import { Sparkline } from "@/components/Sparkline";
import { DataTable, type Column } from "@/components/ui/DataTable";
import {
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  PageHeader,
  Section,
} from "@/components/ui/primitives";
import { StatTile } from "@/components/ui/stats";
import { DirectionTag, Money, SymbolLink } from "@/components/ui/cells";
import {
  getWatchlist,
  getWatchlistQuotes,
  quotesWsUrl,
  type QuotesMessage,
  type WatchlistResponse,
  type WatchlistRow,
} from "@/lib/api";
import { ago, clock, countCompact, inr, inrPrice, pctSigned, signCls } from "@/lib/format";

const REFRESH_MS = 15000;
// Traded volume comes from the latest-tick store the live loop writes, not the WS price
// stream — so it needs its own poll. Cheap: a DB read, never a second broker connection.
const VOLUME_MS = 2000;
const SPARK_POINTS = 40; // rolling LTP buffer length per symbol for the sparkline
// A quote stream that has gone this long without a tick is stale, whatever the
// socket says — the market is closed or the feed died. Never a decorative pulse.
const TICK_STALE_MS = 20000;

export default function WatchlistPage() {
  const [data, setData] = useState<WatchlistResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  // Live quotes (WebSocket): latest LTP per symbol + a rolling buffer for the sparkline.
  const [quotes, setQuotes] = useState<Record<string, number>>({});
  const [buffers, setBuffers] = useState<Record<string, number[]>>({});
  const [connected, setConnected] = useState(false);
  const [lastTick, setLastTick] = useState<number | null>(null);
  // Cumulative day volume per symbol, from the latest-tick snapshot.
  const [volumes, setVolumes] = useState<Record<string, number | null>>({});
  // Re-render on a timer so "last tick 34s ago" ages without a new tick arriving.
  const [, setClockTick] = useState(0);
  const stoppedRef = useRef(false);

  const load = useCallback((spinner = true) => {
    if (spinner) setLoading(true);
    setError(null);
    getWatchlist()
      .then((d) => {
        setData(d);
        setLastRefresh(new Date());
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => load(false), REFRESH_MS);
    return () => clearInterval(id);
  }, [auto, load]);

  useEffect(() => {
    const id = setInterval(() => setClockTick((n) => n + 1), 5000);
    return () => clearInterval(id);
  }, []);

  // Traded volume poll. Best-effort: the table renders fine without it.
  useEffect(() => {
    let cancelled = false;
    const pull = () =>
      getWatchlistQuotes()
        .then((q) => {
          if (cancelled) return;
          const next: Record<string, number | null> = {};
          for (const s of q.symbols) next[s.symbol] = s.volume;
          setVolumes(next);
        })
        .catch(() => {
          /* volume is supplementary — never surface it as a page error */
        });
    pull();
    const id = setInterval(pull, VOLUME_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Live LTP stream over WebSocket — updates every ~1s; auto-reconnects on drop.
  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    stoppedRef.current = false;
    const connect = () => {
      try {
        ws = new WebSocket(quotesWsUrl(1));
      } catch {
        return;
      }
      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => {
        let msg: QuotesMessage;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (!msg.quotes) return;
        const q = msg.quotes;
        setLastTick(Date.now());
        setQuotes((prev) => ({ ...prev, ...q }));
        setBuffers((prev) => {
          const next = { ...prev };
          for (const [sym, px] of Object.entries(q)) {
            const arr = (next[sym] || []).concat(px);
            next[sym] = arr.length > SPARK_POINTS ? arr.slice(-SPARK_POINTS) : arr;
          }
          return next;
        });
      };
      ws.onclose = () => {
        setConnected(false);
        if (!stoppedRef.current) retry = setTimeout(connect, 3000);
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
      };
    };
    connect();
    return () => {
      stoppedRef.current = true;
      if (retry) clearTimeout(retry);
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
  }, []);

  if (error && !data) {
    return (
      <>
        <PageHeader title="Watchlist" />
        <ErrorBanner>Could not load the watchlist: {error}</ErrorBanner>
      </>
    );
  }
  if (!data) {
    return (
      <>
        <PageHeader title="Watchlist" />
        <LoadingBlock label="Loading the watchlist" />
      </>
    );
  }

  const tickAge = lastTick == null ? null : (Date.now() - lastTick) / 1000;
  const feedState = !connected
    ? "off"
    : lastTick != null && Date.now() - lastTick < TICK_STALE_MS
      ? "live"
      : "stale";
  const feedLabel =
    feedState === "live"
      ? `Live prices — last tick ${ago(tickAge)}`
      : feedState === "stale"
        ? `Prices held — no tick ${tickAge == null ? "since the page opened" : ago(tickAge)} (market closed or feed down)`
        : "Quote stream offline — reconnecting";

  const open = data.symbols.filter((r) => r.open_position);
  const basket = [...data.symbols].sort((a, b) => {
    const rank = (r: WatchlistRow) => (r.trades_today > 0 ? 0 : 1);
    return rank(a) !== rank(b) ? rank(a) - rank(b) : a.symbol.localeCompare(b.symbol);
  });

  const priceCell = (r: WatchlistRow) => {
    const ltp = quotes[r.symbol];
    const buf = buffers[r.symbol];
    const prev = buf && buf.length >= 2 ? buf[buf.length - 2] : undefined;
    const tick = ltp != null && prev != null ? (ltp > prev ? "pos" : ltp < prev ? "neg" : "") : "";
    return (
      <span className={`mono ${tick}`}>{ltp != null ? inrPrice(ltp) : "—"}</span>
    );
  };

  const volumeCell = (r: WatchlistRow) => (
    <span className="mono">{countCompact(volumes[r.symbol])}</span>
  );

  const volumeColumn: Column<WatchlistRow> = {
    id: "volume",
    header: (
      <>
        Volume
        <InfoTip
          full="Traded volume"
          def="Cumulative shares traded in this name so far today, from the live feed. Holds at the closing figure once the market shuts."
        />
      </>
    ),
    label: "Traded volume",
    numeric: true,
    hideOnStack: true,
    cell: volumeCell,
  };

  const openColumns: Column<WatchlistRow>[] = [
    {
      id: "symbol",
      header: "Symbol",
      primary: true,
      cell: (r) => (
        <>
          <SymbolLink symbol={r.symbol} />
          <span className="stack-only">{priceCell(r)}</span>
        </>
      ),
    },
    {
      id: "price",
      header: (
        <>
          Live ₹
          <InfoTip
            full="Live price"
            def="Last traded price, streamed ~1s from the Dhan feed. Ticks during market hours; holds the last traded price when the market is closed."
          />
        </>
      ),
      label: "Live price",
      numeric: true,
      hideOnStack: true,
      cell: priceCell,
    },
    volumeColumn,
    {
      id: "status",
      header: (
        <>
          Side
          <InfoTip term="direction" />
        </>
      ),
      label: "Side",
      cell: (r) => <DirectionTag direction={r.open_position?.direction} />,
    },
    {
      id: "unreal",
      header: (
        <>
          Unrealized
          <InfoTip term="unrealized_pnl" />
        </>
      ),
      label: "Unrealized",
      numeric: true,
      cell: (r) => (
        <span className={signCls(r.open_position?.unrealized_pnl_pct)}>
          {pctSigned(r.open_position?.unrealized_pnl_pct)}
        </span>
      ),
    },
    {
      id: "entry",
      header: (
        <>
          Entry ₹<InfoTip term="entry" />
        </>
      ),
      label: "Entry",
      numeric: true,
      cell: (r) => <Money value={r.open_position?.entry} kind="price" />,
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
      cell: (r) => (
        <>
          <Money value={r.open_position?.target} kind="price" />
          {r.open_position?.target_pct != null && (
            <span className="faint small"> ({pctSigned(r.open_position.target_pct)})</span>
          )}
        </>
      ),
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
      cell: (r) => (
        <>
          <Money value={r.open_position?.stop_loss} kind="price" />
          {r.open_position?.stop_pct != null && (
            <span className="faint small"> (−{r.open_position.stop_pct.toFixed(2)}%)</span>
          )}
        </>
      ),
    },
  ];

  const basketColumns: Column<WatchlistRow>[] = [
    {
      id: "symbol",
      header: "Symbol",
      primary: true,
      cell: (r) => (
        <>
          <SymbolLink symbol={r.symbol} />
          <span className="stack-only">{priceCell(r)}</span>
        </>
      ),
    },
    {
      id: "price",
      header: "Live ₹",
      label: "Live price",
      numeric: true,
      hideOnStack: true,
      cell: priceCell,
    },
    volumeColumn,
    {
      id: "trend",
      header: (
        <>
          Trend
          <InfoTip
            full="Intraday trend"
            def="Sparkline of the recent live prices this session (rolling ~40 samples). Green = up over the window, red = down."
          />
        </>
      ),
      label: "Trend",
      cell: (r) =>
        (buffers[r.symbol]?.length ?? 0) >= 2 ? (
          <Sparkline points={buffers[r.symbol]} label={r.symbol} />
        ) : null,
    },
    {
      id: "sector",
      header: (
        <>
          Sector
          <InfoTip term="sector" />
        </>
      ),
      label: "Sector",
      hideOnStack: true,
      cell: (r) => <span className="muted">{r.sector || "—"}</span>,
    },
    {
      id: "today",
      header: (
        <>
          Today
          <InfoTip
            full="Today's activity"
            def="Number of paper trades on this name today and their net ₹ P&L."
          />
        </>
      ),
      label: "Today",
      numeric: true,
      cell: (r) =>
        r.trades_today > 0 ? (
          <span className={signCls(r.pnl_today)}>
            {r.trades_today} · {inr(r.pnl_today)}
          </span>
        ) : null,
      sortBy: (r) => r.pnl_today,
    },
    {
      id: "alltime",
      header: (
        <>
          All-time
          <InfoTip
            full="All-time trades"
            def="Total paper trades recorded on this name across all sessions."
          />
        </>
      ),
      label: "All-time trades",
      numeric: true,
      hideOnStack: true,
      cell: (r) => r.trades_total || null,
      sortBy: (r) => r.trades_total,
    },
  ];

  return (
    <>
      <PageHeader
        title="Watchlist"
        eyebrow={`Session ${data.date}`}
        lede={
          <>
            The fixed intraday paper-trading basket — {data.count} liquid,
            sector-diversified NSE names. The live feed subscribes to exactly these and
            paper-trades signals on them through the session. The strategy is selective,
            so most names sit flat most of the time.{" "}
            <strong>Decision-support only — no live orders.</strong>
          </>
        }
      />

      <div className="feed-status" data-state={feedState} role="status">
        <span className="feed-dot" aria-hidden="true" />
        <span className="feed-label">{feedLabel}</span>
        <span className="feed-spacer" />
        <span className="feed-actions">
          {lastRefresh && <span className="faint tiny">table {clock(lastRefresh)}</span>}
          <label className="checkbox tiny">
            <input
              type="checkbox"
              checked={auto}
              onChange={() => setAuto((a) => !a)}
            />
            auto
          </label>
          <button
            type="button"
            className="secondary"
            onClick={() => load(true)}
            disabled={loading}
          >
            {loading ? "…" : "Refresh"}
          </button>
        </span>
      </div>

      <div className="stat-grid">
        <StatTile label="Symbols watched" value={String(data.count)} emphasis="quiet" />
        <StatTile label="Open now" value={String(data.open_now)} emphasis="quiet" />
        <StatTile label="Traded today" value={String(data.traded_today)} emphasis="quiet" />
      </div>

      <Section
        title={`Open now (${open.length})`}
        note="Live trade levels. Entry, target and stop only exist while a position is open."
      >
        <DataTable
          label="Open positions in the watchlist"
          columns={openColumns}
          rows={open}
          rowKey={(r) => r.symbol}
          rowFlag={() => "active"}
          empty={
            <EmptyState title="Nothing open">
              Positions appear here the moment the engine fills one.
            </EmptyState>
          }
        />
      </Section>

      <Section
        title={`The basket (${data.count} names)`}
        note="Names that traded today sort first. Tap a symbol for its live chart and full paper-trade history."
      >
        <DataTable
          label="Watchlist basket"
          columns={basketColumns}
          rows={basket}
          rowKey={(r) => r.symbol}
          rowFlag={(r) => (r.open_position ? "active" : undefined)}
        />
      </Section>
    </>
  );
}
