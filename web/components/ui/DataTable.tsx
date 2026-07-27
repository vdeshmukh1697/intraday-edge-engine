"use client";

import { useMemo, useState, type ReactNode } from "react";

// One table implementation for the whole dashboard.
//
// The 375px layout is the BASE: each row renders as a labelled card (the header
// row is hidden and every cell carries its own label). From 40rem the real table
// returns. Wide research tables opt into `mobile="scroll"` instead and scroll
// inside their own container — the body never scrolls sideways.

export interface Column<T> {
  /** Stable id; also the sort key and the fallback stack label. */
  id: string;
  /** Header content — may include an <InfoTip/>. */
  header: ReactNode;
  /** Plain-text label for the mobile card stack. Defaults to `id`. */
  label?: string;
  /** Right-align + tabular numerals. */
  numeric?: boolean;
  cell: (row: T) => ReactNode;
  /** Dropped from the mobile card stack to keep cards glanceable. */
  hideOnStack?: boolean;
  /** Rendered as the card's title line in the stack. At most one per table. */
  primary?: boolean;
  /** Full-width block in the stack (chip rows, long messages). */
  spanOnStack?: boolean;
  /** Provide to make the column sortable. */
  sortBy?: (row: T) => number | string;
}

export interface DataTableProps<T> {
  /** Accessible name for the table. Required — screen readers need it. */
  label: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Visible caption under the accessible name (scope, units, caveats). */
  caption?: ReactNode;
  mobile?: "stack" | "scroll";
  /** Cap the height and scroll vertically, so the sticky header earns its keep. */
  tall?: boolean;
  empty?: ReactNode;
  /** Marks a row as live/open — drawn as an edge rule, not a colour wash alone. */
  rowFlag?: (row: T) => "active" | undefined;
  /** Extra explanatory line rendered under a row (the portfolio "Why: …"). */
  subRow?: (row: T) => ReactNode;
  initialSort?: { id: string; asc: boolean };
  footNote?: ReactNode;
}

export function DataTable<T>({
  label,
  columns,
  rows,
  rowKey,
  caption,
  mobile = "stack",
  tall = false,
  empty,
  rowFlag,
  subRow,
  initialSort,
  footNote,
}: DataTableProps<T>) {
  const [sort, setSort] = useState(initialSort ?? null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.id === sort.id);
    if (!col?.sortBy) return rows;
    const key = col.sortBy;
    return [...rows].sort((a, b) => {
      const av = key(a);
      const bv = key(b);
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sort.asc ? cmp : -cmp;
    });
  }, [rows, sort, columns]);

  const toggleSort = (id: string) =>
    setSort((prev) => (prev?.id === id ? { id, asc: !prev.asc } : { id, asc: true }));

  if (rows.length === 0 && empty) {
    return <>{empty}</>;
  }

  return (
    <div className="dt-frame">
      <div className="dt-scroll" data-tall={tall || undefined}>
        <table className="dt" data-mobile={mobile} aria-label={label}>
          {caption && <caption>{caption}</caption>}
          <thead>
            <tr>
              {columns.map((c) => {
                const active = sort?.id === c.id;
                return (
                  <th
                    key={c.id}
                    className={c.numeric ? "num" : undefined}
                    aria-sort={
                      active ? (sort.asc ? "ascending" : "descending") : undefined
                    }
                    scope="col"
                  >
                    {c.sortBy ? (
                      <button
                        type="button"
                        className="dt-sort"
                        onClick={() => toggleSort(c.id)}
                      >
                        {c.header}
                        <span aria-hidden="true">
                          {active ? (sort.asc ? "▲" : "▼") : "↕"}
                        </span>
                      </button>
                    ) : (
                      c.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const sub = subRow?.(row);
              return (
                <Row
                  key={rowKey(row)}
                  row={row}
                  columns={columns}
                  flag={rowFlag?.(row)}
                  sub={sub}
                />
              );
            })}
          </tbody>
        </table>
      </div>
      {mobile === "scroll" && (
        <p className="dt-hint" data-only="stack">
          Scroll the table sideways to see every column.
        </p>
      )}
      {footNote && <p className="dt-hint">{footNote}</p>}
    </div>
  );
}

function Row<T>({
  row,
  columns,
  flag,
  sub,
}: {
  row: T;
  columns: Column<T>[];
  flag?: "active";
  sub?: ReactNode;
}) {
  return (
    <>
      <tr data-flag={flag} data-hassub={sub ? "true" : undefined}>
        {columns.map((c) => {
          const content = c.cell(row);
          // A cell that renders nothing is dropped from the card stack rather than
          // filling it with labelled dashes — most rows in a selective strategy
          // have nothing to say about most columns.
          const empty = content == null || content === "" || content === false;
          return (
            <td
              key={c.id}
              className={c.numeric ? "num" : undefined}
              data-label={c.label ?? c.id}
              data-primary={c.primary ? "true" : undefined}
              data-hide-stack={c.hideOnStack ? "true" : undefined}
              data-span={c.spanOnStack ? "true" : undefined}
              data-empty={empty ? "true" : undefined}
            >
              {content}
            </td>
          );
        })}
      </tr>
      {sub && (
        <tr className="why-row">
          <td colSpan={columns.length} data-span="true">
            {sub}
          </td>
        </tr>
      )}
    </>
  );
}
