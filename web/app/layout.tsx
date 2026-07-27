import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";
import AuthGate from "@/components/AuthGate";

export const metadata: Metadata = {
  title: "Signal Engine Dashboard",
  description:
    "Read-only decision-support dashboard for the intraday signal engine. Paper money only — no live orders are ever placed.",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
};

// Runs before first paint so a stored light/dark choice never flashes the other
// theme. Kept to one expression; the toggle in <Nav> owns everything after this.
const THEME_BOOTSTRAP = `try{var t=localStorage.getItem("signal-engine-theme");if(t==="light"||t==="dark"){document.documentElement.dataset.theme=t}}catch(e){}`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <div className="app-shell">
          <Nav />
          <main className="page-main" id="main">
            <AuthGate>{children}</AuthGate>
          </main>
          <footer className="site-footer">
            <div className="site-footer-inner">
              <p>
                <strong>Paper money only — no live orders are ever placed.</strong>{" "}
                Simulated fills against real prices, with a real cost model.
              </p>
              <p>
                Decision-support only. Not investment advice. The strategy has no
                demonstrated edge; scores shown are uncalibrated. Intraday trading
                carries substantial risk of loss.
              </p>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
