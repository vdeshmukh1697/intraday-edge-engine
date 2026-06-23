import { NextRequest, NextResponse } from "next/server";

// Dhan's registered redirect target. After OTP, Dhan sends the browser here with ?tokenId.
// This server-side route hands the tokenId to the backend (which holds the API secret and
// does the token exchange + persistence), then bounces the user back to the dashboard.
// The backend URL is whatever the dashboard already talks to (refreshed per tunnel run).
const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"
).replace(/\/$/, "");

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const tokenId = req.nextUrl.searchParams.get("tokenId");
  const home = (q: string) => NextResponse.redirect(new URL(`/?auth=${q}`, req.url));

  if (!tokenId) return home("missing_token");
  try {
    const r = await fetch(
      `${API_BASE}/api/auth/dhan/consume?tokenId=${encodeURIComponent(tokenId)}`,
      { cache: "no-store" }
    );
    return home(r.ok ? "ok" : "failed");
  } catch {
    return home("backend_unreachable");
  }
}
