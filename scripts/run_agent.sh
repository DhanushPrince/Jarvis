#!/usr/bin/env bash
# run_agent.sh — Start the Pipecat voice agent server (bot.py) with 8GB-safe
# defaults. The local LLM (./scripts/start_llm.sh) must already be running on :1234.
#
#   Terminal A:  ./scripts/start_llm.sh
#   Terminal B:  ./scripts/run_agent.sh
#   Terminal C:  cd client && npm i && npm run dev
#
# Env overrides (all optional):
#   STT_ENGINE=whisper|parakeet   (default whisper)
#   WHISPER_MODEL=...             (default mlx-community/whisper-base-mlx)
#   PARAKEET_MODEL=...            (default sonic-speech/parakeet-tdt-0.6b-v3-int8)
#   LLM_MODEL=...                 (default mlx-community/Qwen3-0.6B-4bit)
#   HF_HUB_OFFLINE=1              (set once models are cached, for full offline)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER="$REPO/server"
VENV="$SERVER/.venv"

cd "$SERVER"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

export STT_ENGINE="${STT_ENGINE:-whisper}"
export WHISPER_MODEL="${WHISPER_MODEL:-mlx-community/whisper-base-mlx}"
export PARAKEET_MODEL="${PARAKEET_MODEL:-sonic-speech/parakeet-tdt-0.6b-v3-int8}"
export LLM_MODEL="${LLM_MODEL:-mlx-community/Qwen3-0.6B-4bit}"
export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:1234/v1}"

echo "==> Starting bot.py"
echo "    STT_ENGINE   = $STT_ENGINE"
echo "    WHISPER_MODEL= $WHISPER_MODEL"
echo "    LLM_MODEL    = $LLM_MODEL  ($LLM_BASE_URL)"
echo "    (first boot may take >=30s to load models; open http://localhost:7860)"
exec python bot.py --host localhost --port 7860
