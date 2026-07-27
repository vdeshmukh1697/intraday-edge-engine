"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import ThemeToggle from "@/components/ThemeToggle";

const LINKS = [
  { href: "/portfolio", label: "Portfolio" },
  { href: "/", label: "Leaderboard" },
  { href: "/watchlist", label: "Watchlist" },
  { href: "/premarket", label: "Pre-market" },
  { href: "/paper", label: "Paper trading" },
  { href: "/predictions", label: "Predictions" },
  { href: "/movers", label: "Movers" },
  { href: "/backtest", label: "Backtest" },
];

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

/**
 * One route list, two layouts: a disclosure drawer below 64rem (eight routes never
 * fitted a phone-width row) and an inline row above it. The current route is
 * marked with aria-current, so it is announced as well as drawn.
 */
export default function Nav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  // Navigating with the drawer open should close it.
  useEffect(() => setOpen(false), [pathname]);

  const current = LINKS.find((l) => isActive(pathname, l.href));

  return (
    <nav className="nav" aria-label="Dashboard sections">
      <div className="nav-bar">
        <Link href="/portfolio" className="nav-brand">
          SIGNAL ENGINE
          <small>Paper money · no live orders</small>
        </Link>
        <span className="nav-spacer" />
        <span className="nav-current">{current?.label ?? "Stock"}</span>
        <ThemeToggle />
        <button
          type="button"
          className="icon-btn nav-menu-btn"
          aria-expanded={open}
          aria-controls="nav-links"
          onClick={() => setOpen((o) => !o)}
        >
          <span aria-hidden="true">{open ? "✕" : "☰"}</span>
          <span className="visually-hidden">
            {open ? "Close navigation" : "Open navigation"}
          </span>
        </button>
        <ul id="nav-links" className="nav-links" data-open={open}>
          {LINKS.map((l) => (
            <li key={l.href}>
              <Link
                href={l.href}
                className="nav-link"
                aria-current={isActive(pathname, l.href) ? "page" : undefined}
              >
                {l.label}
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
