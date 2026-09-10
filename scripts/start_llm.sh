#!/usr/bin/env bash
# start_llm.sh — Start a local OpenAI-compatible LLM server on 127.0.0.1:1234
# using mlx_lm.server with Qwen3-0.6B-4bit (~0.6GB). Fully local, no cloud.
#
#   cd <repo>
#   ./scripts/start_llm.sh          # runs in foreground (keep terminal open)
#
# The model NAME the server exposes at /v1/models is the HF repo id below.
# bot.py must use the same name (LLM_MODEL). run_agent.sh already matches this.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER="$REPO/server"
VENV="$SERVER/.venv"
MODEL="${LLM_MODEL:-mlx-community/Qwen3-0.6B-4bit}"
PORT="${PORT:-1234}"

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "==> Starting mlx_lm.server"
echo "    model: $MODEL"
echo "    url  : http://127.0.0.1:$PORT/v1"
echo "    (first boot downloads the model; keep this terminal open)"
# --prefill-step-size 256 keeps the prefill matrix small (helps on 8GB).
# --chat-template-args '{"enable_thinking": false}' disables Qwen3's reasoning
# mode SERVER-SIDE for every request. Qwen3 is a reasoning model; with thinking
# ON it returns reasoning_content and EMPTY content (the bot would try to
# *speak* its chain-of-thought). Disabling yields clean content for the TTS.
TEMPLATE_ARGS='{"enable_thinking": false}'
if command -v mlx_lm.server >/dev/null 2>&1; then
  exec mlx_lm.server --model "$MODEL" --host 127.0.0.1 --port "$PORT" \
    --prefill-step-size 256 --chat-template-args "$TEMPLATE_ARGS"
else
  exec python -m mlx_lm.server --model "$MODEL" --host 127.0.0.1 --port "$PORT" \
    --prefill-step-size 256 --chat-template-args "$TEMPLATE_ARGS"
fi
