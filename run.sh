#!/usr/bin/env bash
# Convenience wrapper: activates the project venv and forwards args to the CLI.
# Usage:  ./run.sh scan --top 20
#         ./run.sh replay --demo
#         ./run.sh backtest --days 10
# So you never have to remember to `source .venv/bin/activate` first.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "$DIR/.venv" ]; then
  echo "No .venv found at $DIR/.venv" >&2
  echo "First-time setup:" >&2
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && pip install -e ." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$DIR/.venv/bin/activate"
exec python -m signal_engine.cli "$@"
