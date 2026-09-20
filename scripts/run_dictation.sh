#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$(cd "$SCRIPT_DIR/../server" && pwd)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Jarvis Dictation requires macOS." >&2
  exit 2
fi
if [[ ! -x "$SERVER/.venv/bin/python" ]]; then
  echo "Run WITH_PARAKEET=1 ./scripts/setup_server.sh first." >&2
  exit 2
fi

cd "$SERVER"
exec .venv/bin/python -m dictation.app "$@"
