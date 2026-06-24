"use client";

import { useEffect, useState } from "react";
import { getAuthStatus, dhanLoginUrl, type AuthStatus } from "@/lib/api";

// Gates the dashboard on Dhan token health. When the (paid, real-time) Dhan source is the
// backend and its 24h token has expired, we block the UI and offer a one-tap OTP reconnect
// that round-trips through Dhan and back to the dashboard. For any non-Dhan source the gate
// is transparent.
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getAuthStatus()
      .then((s) => alive && setStatus(s))
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, []);

  // Backend unreachable. Previously this silently rendered the app, which looked like
  // "the dashboard opens with no OTP prompt" when the Cloudflare tunnel was actually down.
  // Surface a clear banner so the cause is obvious (and the Dhan reconnect is reachable
  // again once the tunnel is back), while still rendering the app below it.
  if (error) {
    return (
      <>
        <div
          style={{
            background: "#7f1d1d",
            color: "#fff",
            padding: "10px 16px",
            fontSize: 14,
            textAlign: "center",
          }}
        >
          ⚠️ Can&apos;t reach the engine backend — live data &amp; Dhan OTP reconnect are
          unavailable. The Cloudflare tunnel may be down; restart it (run-with-tunnel.sh).
        </div>
        {children}
      </>
    );
  }
  if (!status) return null; // brief: status in flight

  if (status.auth_required && !status.connected) {
    return (
      <div className="auth-gate">
        <div className="auth-card">
          <h2>Reconnect Dhan</h2>
          <p>
            Your Dhan session has expired (tokens last 24h). Log in with OTP to
            resume live data — you&apos;ll be brought right back here.
          </p>
          <a className="auth-btn" href={dhanLoginUrl()}>
            Log in to Dhan
          </a>
          <p className="auth-note">
            You&apos;ll enter your OTP on Dhan&apos;s secure page; the dashboard
            never sees your password.
          </p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
