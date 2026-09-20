#!/usr/bin/env bash
# setup_server.sh — Create a Python 3.12 venv for the Pipecat voice agent server
# and install deps.
#
#   cd <repo>
#   ./scripts/setup_server.sh
#
# Notes:
#   1. Forces Python 3.12 (3.13/3.14 lack compatible wheels).
#   2. torch IS required (misaki[en] for Kokoro TTS imports it); we keep it.
#      STT/LLM stay torch-free at runtime — torch only loads in the TTS subprocess.
#   3. Retries downloads with resume on flaky networks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER="$REPO/server"
VENV="$SERVER/.venv"

# --- Pick Python 3.12 explicitly -------------------------------------------
PY=""
for c in python3.12 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [[ -z "$PY" ]]; then
  echo "ERROR: python3.12 not found. Install it:  brew install python@3.12" >&2
  exit 1
fi
echo "==> Using interpreter: $("$PY" --version 2>&1)"

cd "$SERVER"

# --- Recreate venv if missing or not 3.12 ----------------------------------
RECREATE=0
if [[ ! -d "$VENV" ]]; then RECREATE=1; fi
if [[ -d "$VENV" ]] && ! "$VENV/bin/python" -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" 2>/dev/null; then
  echo "==> Existing venv is not Python 3.12 — recreating"
  RECREATE=1
fi
if [[ "${FORCE_RECREATE:-0}" == "1" ]]; then RECREATE=1; fi
if [[ "$RECREATE" == "1" ]]; then
  rm -rf "$VENV"
  "$PY" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -U pip wheel setuptools

echo "==> Installing server requirements (this can take a few minutes)"
PIP="python -m pip install -U --resume-retries 10"
$PIP -r requirements.txt

# mlx_lm.server for the local LLM
$PIP mlx-lm

# pipecat's whisper module imports faster_whisper at load time even for the MLX
# class, so it must be installed (CTranslate2 backend, not torch).
$PIP faster-whisper

# Kokoro TTS needs misaki[en] for English G2P, which requires torch + spacy +
# transformers. English Kokoro TTS genuinely needs torch (loads only in the
# isolated Kokoro subprocess; STT/LLM remain torch-free at runtime).
$PIP "misaki[en]"
python -m spacy download en_core_web_sm 2>/dev/null || true

# Optional Parakeet STT (only needed if STT_ENGINE=parakeet)
if [[ "${WITH_PARAKEET:-0}" == "1" ]]; then
  echo "==> Installing parakeet-mlx (optional STT)"
  $PIP parakeet-mlx
fi
if [[ "${WITH_CLEANUP:-0}" == "1" ]]; then
  echo "==> Installing llama-cpp-python (optional s1-mini cleanup)"
  CMAKE_ARGS="-DGGML_METAL=on" $PIP llama-cpp-python
fi

# --- Verify imports ---------------------------------------------------------
echo "==> Verifying imports"
python - <<'PY'
import importlib, sys
mods = ["pipecat", "mlx", "mlx_lm", "mlx_audio", "aiortc", "fastapi", "uvicorn"]
ok = True
for m in mods:
    try:
        importlib.import_module(m); print("OK   ", m)
    except Exception as e:
        ok = False; print("FAIL ", m, "-", type(e).__name__, e)
try:
    from pipecat.services.whisper.stt import WhisperSTTServiceMLX, MLXModel  # noqa
    print("OK    pipecat WhisperSTTServiceMLX")
except Exception as e:
    ok = False; print("FAIL  pipecat WhisperSTTServiceMLX -", e)
print("PYTHON", sys.version)
sys.exit(0 if ok else 1)
PY

echo "==> Setup complete. Next: ./scripts/start_llm.sh   (then ./scripts/smoke_test.sh)"
