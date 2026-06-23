import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "Signal Engine Dashboard",
  description:
    "Read-only decision-support dashboard for the intraday signal engine.",
};

const DISCLAIMER =
  "Decision-support only. Not investment advice. No live orders. Intraday trading carries substantial risk of loss.";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="app-shell">
          <Nav />
          <main>{children}</main>
          <footer className="footer">
            <span className="disclaimer">{DISCLAIMER}</span>
          </footer>
        </div>
      </body>
    </html>
  );
}
