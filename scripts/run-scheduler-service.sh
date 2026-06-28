#!/usr/bin/env bash
# launchd entrypoint for the Signal Engine daily SCHEDULER (the trading orchestrator).
#
# Runs `cli schedule` (APScheduler), which fires the IST jobs that constitute the live day:
#   06:00 Dhan token renew · 08:00 archive · 08:30 pre-market · 09:15 LIVE feed→close ·
#   15:45 scan · 16:10 archive. Paper signals + alerts only — never places orders.
#
# Why a service: nothing previously kept the scheduler alive, so on laptop sleep/crash/logout it
# died — which silently broke BOTH the 09:15 paper-trading session AND the 06:00 daily token
# renewal (so the Dhan token lapsed). KeepAlive + RunAtLoad makes it self-healing.
# Managed by ~/Library/LaunchAgents/com.vikrant.signal-engine-scheduler.plist.
#
# NOTE: the 09:15 live job needs a VALID Dhan token in .env. The 06:00 renew job keeps an
# already-active token fresh, but once the token has fully EXPIRED it must be re-seeded once via
# the dashboard "Reconnect Dhan" OTP (RenewToken can't revive a dead token).
set -uo pipefail

export HOME="${HOME:-/Users/vikrantdeshmukh}"
cd "/Users/vikrantdeshmukh/Personal projects" || exit 1
exec ./run.sh schedule
