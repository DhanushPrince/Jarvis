#!/usr/bin/env bash
# start.sh — One-command bring-up for the Jarvis voice agent.
# Starts the local LLM server (background), the agent server (background),
# and the web client dev server (background).
#
#   cd <repo>
#   ./scripts/start.sh
#
# To stop everything:
#   ./scripts/stop.sh
set -uo pipefail

# --- Resolve repo paths relative to this script (portable) ------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"      # macos-local-voice-agents/
SERVER="$REPO/server"
CLIENT="$REPO/client"
VENV="$SERVER/.venv"
LLM_PORT="${PORT:-1234}"
AGENT_PORT=7860
CLIENT_PORT=3000

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# --- Kill any existing processes on our ports ---
say "Cleaning up old processes"
for p in $LLM_PORT $AGENT_PORT $CLIENT_PORT; do
  lsof -ti:$p | xargs kill -9 2>/dev/null || true
done
pkill -f mlx_lm.server 2>/dev/null || true
pkill -f "python bot.py" 2>/dev/null || true
sleep 1

# --- Preflight ---
say "Preflight checks"
PY=""
for c in python3.12 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[[ -n "$PY" ]] || die "python3.12 not found. brew install python@3.12"
echo "    python : $("$PY" --version 2>&1)"
command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg not found. brew install ffmpeg"
[[ "$(uname -m)" == "arm64" ]] || die "Not Apple Silicon (arm64)."
[[ -x "$VENV/bin/python" ]] || die "venv missing at $VENV — run ./scripts/setup_server.sh first."

# --- 1. LLM Server (background) ---
say "Starting LLM server on :$LLM_PORT"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
if curl -sf "http://127.0.0.1:$LLM_PORT/v1/models" >/dev/null 2>&1; then
  echo "    LLM already running."
else
  nohup "$SCRIPT_DIR/start_llm.sh" >"$REPO/llm_server.log" 2>&1 &
  LLM_PID=$!
  echo "    Waiting for model to load (first boot downloads weights)..."
  for i in $(seq 1 150); do
    if curl -sf "http://127.0.0.1:$LLM_PORT/v1/models" >/dev/null 2>&1; then
      echo "    LLM ready (~$((i*2))s)"
      break
    fi
    if ! kill -0 "$LLM_PID" 2>/dev/null; then
      echo "    --- last 20 lines of log ---"
      tail -n 20 "$REPO/llm_server.log" || true
      die "LLM server exited early."
    fi
    sleep 2
  done
  curl -sf "http://127.0.0.1:$LLM_PORT/v1/models" >/dev/null 2>&1 \
    || die "LLM not ready in time."
fi

# --- 2. Agent Server (background) ---
say "Starting agent server on :$AGENT_PORT"
cd "$SERVER"
nohup "$VENV/bin/python" bot.py --host localhost --port "$AGENT_PORT" >"$REPO/agent_server.log" 2>&1 &
AGENT_PID=$!
echo "    Agent PID: $AGENT_PID"
sleep 3
if curl -sf http://localhost:$AGENT_PORT/api/offer >/dev/null 2>&1 || \
   [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:$AGENT_PORT/api/offer 2>/dev/null)" = "405" ]; then
  echo "    Agent ready."
else
  echo "    --- last 20 lines of log ---"
  tail -n 20 "$REPO/agent_server.log" || true
  die "Agent server failed to start."
fi

# --- 3. Client (background) ---
say "Starting client on :$CLIENT_PORT"
cd "$CLIENT"
nohup npx next dev -p "$CLIENT_PORT" >"$REPO/client.log" 2>&1 &
CLIENT_PID=$!
echo "    Client PID: $CLIENT_PID"
echo "    Waiting for client to compile..."
for i in $(seq 1 30); do
  if curl -sf "http://localhost:$CLIENT_PORT" >/dev/null 2>&1; then
    echo "    Client ready."
    break
  fi
  sleep 2
done

# --- Done ---
say "Jarvis is running!"
cat <<EOF
  LLM     : http://127.0.0.1:$LLM_PORT
  Agent   : http://localhost:$AGENT_PORT
  Client  : http://localhost:$CLIENT_PORT

Open http://localhost:$CLIENT_PORT in your browser, allow mic,
and say "Hello" to Jarvis.

Logs: $REPO/{llm_server,agent_server,client}.log
To stop everything:  ./scripts/stop.sh
EOF
