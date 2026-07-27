import type { ReactNode } from "react";

// Surfaces, headings and state blocks. Every page composes these instead of
// re-implementing its own card/table/empty shell.

export function PageHeader({
  title,
  eyebrow,
  lede,
  aside,
}: {
  title: string;
  /** Small uppercase kicker above the title (e.g. the session date). */
  eyebrow?: ReactNode;
  /** One-paragraph explanation of what the page is — carries the honesty copy. */
  lede?: ReactNode;
  /** Status badge / controls that belong beside the title. */
  aside?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head-top">
        <div className="stack-tight">
          {eyebrow && <span className="page-head-eyebrow">{eyebrow}</span>}
          <h1>{title}</h1>
        </div>
        {aside}
      </div>
      {lede && <p className="page-lede">{lede}</p>}
    </header>
  );
}

/** A plain surface. Use `pad` for free-form content, or compose Section. */
export function Card({
  children,
  pad = true,
  className = "",
}: {
  children: ReactNode;
  pad?: boolean;
  className?: string;
}) {
  return (
    <div className={`card${pad ? " card-pad" : ""}${className ? ` ${className}` : ""}`}>
      {children}
    </div>
  );
}

/**
 * A titled region of a page. Renders a real <section> with an <h2>/<h3> so the
 * document outline matches the visual one.
 */
export function Section({
  title,
  titleNode,
  note,
  aside,
  level = 2,
  children,
}: {
  title?: string;
  /** Title with inline nodes (e.g. an InfoTip) — takes precedence over `title`. */
  titleNode?: ReactNode;
  /** Small print under the title: scope, caveats, honesty labels. */
  note?: ReactNode;
  aside?: ReactNode;
  level?: 2 | 3;
  children: ReactNode;
}) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <section className="card">
      <div className="card-head">
        <div className="card-head-titles">
          <Heading className="card-title">{titleNode ?? title}</Heading>
          {note && <p className="card-note">{note}</p>}
        </div>
        {aside}
      </div>
      <div className="card-body">{children}</div>
    </section>
  );
}

/** Interpretation, caveats and honesty labels — visually distinct from data. */
export function Callout({
  tone = "neutral",
  icon,
  title,
  children,
}: {
  tone?: "neutral" | "info" | "warning" | "danger";
  icon?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <aside className="callout" data-tone={tone}>
      {icon && (
        <span className="callout-icon" aria-hidden="true">
          {icon}
        </span>
      )}
      <div>
        {title && <strong className="callout-title">{title}</strong>}
        {children}
      </div>
    </aside>
  );
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-state-title">{title}</span>
      {children}
    </div>
  );
}

export function ErrorBanner({ children }: { children: ReactNode }) {
  return (
    <div className="banner" role="alert">
      {children}
    </div>
  );
}

/** Loading placeholder that reserves the layout instead of collapsing it. */
export function SkeletonRow({ width = "100%" }: { width?: string }) {
  return <span className="skeleton" style={{ width }} aria-hidden="true" />;
}

export function LoadingBlock({ label, rows = 4 }: { label: string; rows?: number }) {
  const widths = ["70%", "92%", "84%", "60%", "78%", "88%"];
  return (
    <div className="card card-pad" role="status" aria-live="polite">
      <span className="visually-hidden">{label}</span>
      <div className="skeleton-stack">
        {Array.from({ length: rows }, (_, i) => (
          <SkeletonRow key={i} width={widths[i % widths.length]} />
        ))}
      </div>
    </div>
  );
}

export function Badge({
  tone = "neutral",
  size,
  title,
  children,
}: {
  tone?: "neutral" | "positive" | "negative" | "warning" | "info";
  size?: "lg";
  title?: string;
  children: ReactNode;
}) {
  return (
    <span className="badge" data-tone={tone} data-size={size} title={title}>
      {children}
    </span>
  );
}

export function Chip({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <span className="chip" title={title}>
      {children}
    </span>
  );
}

export function ChipRow({ items, max }: { items: string[]; max?: number }) {
  const shown = max ? items.slice(0, max) : items;
  const hidden = items.length - shown.length;
  return (
    <span className="chip-row">
      {shown.map((t) => (
        <Chip key={t}>{t}</Chip>
      ))}
      {hidden > 0 && <Chip title={items.slice(shown.length).join(", ")}>+{hidden}</Chip>}
    </span>
  );
}
