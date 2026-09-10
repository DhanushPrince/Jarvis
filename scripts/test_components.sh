#!/usr/bin/env bash
# test_components.sh — launch the standalone STT/LLM/TTS component tester UI.
#
#   ./scripts/test_components.sh           # starts on http://localhost:8080
#   PORT=8090 ./scripts/test_components.sh  # custom port
#
# Requires: ./scripts/start_llm.sh running (for the LLM panel). STT and TTS
# work without it. Stop with Ctrl+C, or: lsof -ti:8080 | xargs kill
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER="$REPO/server"
VENV="$SERVER/.venv"
PORT="${PORT:-8080}"

[[ -x "$VENV/bin/python" ]] || { echo "venv not found at $VENV (run ./scripts/setup_server.sh)"; exit 1; }

# Free the port if something is already there
lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
sleep 1

echo "==> Component tester starting on http://localhost:$PORT"
echo "    (LLM panel needs ./scripts/start_llm.sh running on :1234)"
exec "$VENV/bin/python" "$SERVER/component_test_server.py" --host localhost --port "$PORT"
