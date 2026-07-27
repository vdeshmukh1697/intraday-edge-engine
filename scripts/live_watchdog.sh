#!/usr/bin/env bash
# Day-keeper for a MANUAL IPv4 live session (date-generic since 2026-07-14).
# The manual `live_ipv4.py live --persist` process is not under launchd, so nothing restarts
# it if the flaky hotspot drops the Dhan WS. This loop keeps a single live process alive
# through the close and logs a heartbeat. Single-writer: it only relaunches when pgrep finds
# NO live process running.
set -uo pipefail
cd "/Users/vikrantdeshmukh/Personal projects"
LOG="logs/live_watchdog_$(TZ=Asia/Kolkata date +%F).log"
LIVE_LOG="logs/manual_live_$(TZ=Asia/Kolkata date +%F).log"

while :; do
  HHMM=$(TZ=Asia/Kolkata date '+%H%M')
  STAMP=$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S IST')
  # Stop after the close (15:31) — the session is done; let EOD jobs take over.
  if [ "$HHMM" -ge 1531 ]; then
    echo "$STAMP watchdog: past 15:31, stopping" >> "$LOG"
    break
  fi
  if [ "$HHMM" -ge 0915 ] && [ "$HHMM" -lt 1531 ]; then
    if ! pgrep -f "live_ipv4.py live" >/dev/null 2>&1; then
      echo "$STAMP watchdog: NO live process — relaunching" >> "$LOG"
      PYTHONUNBUFFERED=1 nohup .venv/bin/python scripts/live_ipv4.py live --persist >> "$LIVE_LOG" 2>&1 &
      sleep 20
    else
      BARS=$(sqlite3 data/signal_engine.sqlite3 "SELECT bars_processed FROM live_status" 2>/dev/null)
      LAST=$(sqlite3 data/signal_engine.sqlite3 "SELECT updated_ts FROM live_status" 2>/dev/null)
      echo "$STAMP watchdog: live OK (bars=$BARS last=$LAST)" >> "$LOG"
    fi
  fi
  sleep 180
done
