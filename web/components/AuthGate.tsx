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

  // Backend unreachable — let the app render; individual pages surface their own errors.
  if (error) return <>{children}</>;
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
