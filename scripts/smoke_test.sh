#!/usr/bin/env bash
# smoke_test.sh — Component inference tests for the local voice stack.
# Runs TTS, STT and LLM independently, logs wall-time + peak RSS, and writes
# machine-readable results to smoke_results.json + smoke_stt.txt (in repo root).
#
# Prereqs:
#   - ./scripts/setup_server.sh has been run (.venv exists)
#   - ./scripts/start_llm.sh is running in another terminal (for the LLM test)
#   - A test clip at $REPO/audio_16k.wav (or $REPO/audio.m4a to auto-convert)
#
#   cd <repo>
#   ./scripts/smoke_test.sh
set -uo pipefail   # NOTE: no -e; we want to record failures, not abort.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER="$REPO/server"
VENV="$SERVER/.venv"
WAV="$REPO/audio_16k.wav"
TTS_OUT="/tmp/jarvis_tts.wav"

STT_ENGINE="${STT_ENGINE:-whisper}"
WHISPER_MODEL="${WHISPER_MODEL:-mlx-community/whisper-base-mlx}"
PARAKEET_MODEL="${PARAKEET_MODEL:-sonic-speech/parakeet-tdt-0.6b-v3-int8}"
LLM_MODEL="${LLM_MODEL:-mlx-community/Qwen3-0.6B-4bit}"
LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:1234/v1}"

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# Ensure the 16k wav exists (regenerate from m4a if needed)
if [[ ! -f "$WAV" ]]; then
  if [[ -f "$REPO/audio.m4a" ]]; then
    echo "==> Regenerating $WAV from audio.m4a"
    ffmpeg -y -i "$REPO/audio.m4a" -ac 1 -ar 16000 -c:a pcm_s16le "$WAV"
  else
    echo "WARNING: no $WAV or audio.m4a found — STT test will fail. Drop a clip at $WAV."
  fi
fi

export ROOT="$REPO" WAV TTS_OUT STT_ENGINE WHISPER_MODEL PARAKEET_MODEL LLM_MODEL LLM_BASE_URL

python - <<'PY'
import json, os, resource, time, traceback, subprocess, sys
from pathlib import Path

root = Path(os.environ["ROOT"])
wav = os.environ["WAV"]
tts_out = os.environ["TTS_OUT"]
stt_engine = os.environ["STT_ENGINE"]
whisper_model = os.environ["WHISPER_MODEL"]
parakeet_model = os.environ["PARAKEET_MODEL"]
llm_model = os.environ["LLM_MODEL"]
llm_base = os.environ["LLM_BASE_URL"]

def rss_mib():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 1)

def mlx_peak():
    try:
        import mlx.core as mx
        return round(mx.get_peak_memory() / (1024 * 1024), 1)
    except Exception:
        return None

results = {}

# ---------------- TTS: Kokoro-82M-bf16 -> /tmp/jarvis_tts.wav ----------------
row = {"role": "TTS", "model": "mlx-community/Kokoro-82M-bf16", "ok": False}
t0 = time.perf_counter()
try:
    try:
        import mlx.core as mx; mx.reset_peak_memory()
    except Exception:
        pass
    import shutil
    for old in Path("/tmp").glob("jarvis_tts*.wav"):
        try: old.unlink()
        except Exception: pass
    base = ["--model", "mlx-community/Kokoro-82M-bf16",
            "--text", "Hello, I'm Jarvis.", "--output", tts_out]
    if shutil.which("mlx-audio.generate"):
        cmd = ["mlx-audio.generate"] + base
    else:
        cmd = [sys.executable, "-m", "mlx_audio.tts.generate"] + base
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    produced = Path(tts_out)
    if not produced.exists():
        cands = sorted(Path("/tmp").glob("jarvis_tts*.wav"), key=lambda x: x.stat().st_mtime)
        if cands:
            produced = cands[-1]
    combined_out = (p.stdout or "") + (p.stderr or "")
    misaki_err = "requires the optional 'misaki'" in combined_out
    size_ok = produced.exists() and produced.stat().st_size > 2000
    tts_ok = size_ok and not misaki_err and p.returncode == 0
    row.update({
        "ok": tts_ok,
        "wav_bytes": produced.stat().st_size if produced.exists() else 0,
        "misaki_error": misaki_err,
        "wall_s": round(time.perf_counter() - t0, 3),
        "peak_rss_mib": rss_mib(),
        "mlx_peak_mib": mlx_peak(),
        "out_file": str(produced) if produced.exists() else None,
        "stdout_tail": (p.stdout or "")[-400:],
        "stderr_tail": (p.stderr or "")[-400:],
    })
    print("TTS ok:", row["ok"], row.get("out_file"))
except Exception as e:
    row.update({"wall_s": round(time.perf_counter()-t0,3), "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-1000:]})
    print("TTS FAIL:", row.get("error"))
results["tts"] = row

# ---------------- STT: transcribe audio_16k.wav ----------------
row = {"role": "STT", "engine": stt_engine, "ok": False}
t0 = time.perf_counter()
try:
    try:
        import mlx.core as mx; mx.reset_peak_memory()
    except Exception:
        pass
    if stt_engine == "parakeet":
        row["model"] = parakeet_model
        from parakeet_mlx import from_pretrained
        model = from_pretrained(parakeet_model)
        res = model.transcribe(wav)
        text = (getattr(res, "text", None) or str(res)).strip()
    else:
        row["model"] = whisper_model
        import mlx_whisper
        res = mlx_whisper.transcribe(wav, path_or_hf_repo=whisper_model)
        text = (res.get("text","") if isinstance(res, dict) else str(res)).strip()
    (root / "smoke_stt.txt").write_text(text + "\n", encoding="utf-8")
    row.update({"ok": bool(text), "wall_s": round(time.perf_counter()-t0,3),
                "peak_rss_mib": rss_mib(), "mlx_peak_mib": mlx_peak(),
                "transcript": text, "out_file": str(root / "smoke_stt.txt")})
    print("STT ok:", row["ok"]); print("  ->", text[:200])
except Exception as e:
    row.update({"wall_s": round(time.perf_counter()-t0,3), "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-1000:]})
    (root / "smoke_stt.txt").write_text(f"[ERROR] {row['error']}\n", encoding="utf-8")
    print("STT FAIL:", row.get("error"))
results["stt"] = row

# ---------------- LLM: one chat completion via local :1234 ----------------
row = {"role": "LLM", "model": llm_model, "base_url": llm_base, "ok": False}
t0 = time.perf_counter()
try:
    import urllib.request
    with urllib.request.urlopen(llm_base.rstrip("/") + "/models", timeout=10) as r:
        models = json.loads(r.read().decode())
    served = [m.get("id") for m in models.get("data", [])]
    row["served_models"] = served
    use_model = llm_model if llm_model in served else (served[0] if served else llm_model)
    row["used_model"] = use_model
    payload = json.dumps({
        "model": use_model,
        "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
        "max_tokens": 128, "temperature": 0.2,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(llm_base.rstrip("/") + "/chat/completions",
                                 data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    msg = resp["choices"][0]["message"]
    reply = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    row["reasoning_only"] = not msg.get("content") and bool(msg.get("reasoning_content"))
    row.update({"ok": bool(reply), "wall_s": round(time.perf_counter()-t0,3), "reply": reply})
    print("LLM ok:", row["ok"]); print("  ->", reply[:200])
except Exception as e:
    row.update({"wall_s": round(time.perf_counter()-t0,3), "error": f"{type(e).__name__}: {e}"})
    print("LLM FAIL:", row.get("error"), "(is ./scripts/start_llm.sh running?)")
results["llm"] = row

out = root / "smoke_results.json"
out.write_text(json.dumps(results, indent=2), encoding="utf-8")
print("\nWrote", out)
PY

echo "==> smoke_test done. See smoke_results.json + smoke_stt.txt"
