#!/usr/bin/env python3
"""Standalone component test server for the Jarvis local voice stack.

Lets you exercise STT, LLM, and TTS *independently* from a single web page,
using the exact same models / wrappers the voice bot uses — but without any
WebRTC / pipecat pipeline in the way. Runs on its own port (default 8080) so it
never interferes with the live voice agent on :7860.

    cd macos-local-voice-agents/server
    .venv/bin/python component_test_server.py --port 8080
    open http://localhost:8080

Endpoints:
    GET  /                -> the test console (single HTML page)
    GET  /api/health      -> which components are reachable
    POST /api/stt         -> multipart file 'audio' -> {"text": ...}
    POST /api/llm         -> json {"prompt": ...}   -> {"reply": ...}
    POST /api/tts         -> json {"text": ...}      -> audio/wav bytes

Config knobs mirror bot.py (env-overridable):
    STT_ENGINE, WHISPER_MODEL, PARAKEET_MODEL, LLM_MODEL, LLM_BASE_URL
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

import uvicorn
from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from loguru import logger

# --- Config (shared with bot.py via config.yaml) -----------------------------
from config import CONFIG

STT_ENGINE = CONFIG["stt"]["engine"]
WHISPER_MODEL = CONFIG["stt"]["whisper_model"]
PARAKEET_MODEL = CONFIG["stt"]["parakeet_model"]
LLM_MODEL = CONFIG["llm"]["model"]
LLM_BASE_URL = CONFIG["llm"]["base_url"]

HERE = Path(__file__).parent
TTS_MODEL = CONFIG["tts"]["model"]
TTS_VOICE = CONFIG["tts"]["voice"]
TTS_SAMPLE_RATE = int(CONFIG["tts"]["sample_rate"])

app = FastAPI(title="Jarvis Component Tester")


# --- TTS worker (reuse the same kokoro_worker.py subprocess protocol) --------
class _KokoroClient:
    """Minimal client for kokoro_worker.py's JSON-over-stdio protocol."""

    def __init__(self, model: str, voice: str):
        self.model = model
        self.voice = voice
        self.proc: subprocess.Popen | None = None
        self.ready = False
        self._lock = asyncio.Lock()
        self._worker = str(HERE / ("marvis_worker.py" if model.startswith("Marvis-AI") else "kokoro_worker.py"))

    def _start(self):
        self.proc = subprocess.Popen(
            [sys.executable, self._worker],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )

    def _cmd(self, obj: dict, timeout: float = 120.0) -> dict:
        if not self.proc or self.proc.poll() is not None:
            self._start()
            self.ready = False
        assert self.proc and self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            return {"error": "worker produced no output (died?)"}
        try:
            return json.loads(line.strip())
        except json.JSONDecodeError:
            return {"error": f"non-JSON from worker: {line[:200]!r}"}

    async def synth(self, text: str) -> bytes:
        """Return 16-bit PCM WAV bytes for `text`."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            if not self.ready:
                r = await loop.run_in_executor(
                    None, self._cmd, {"cmd": "init", "model": self.model, "voice": self.voice}
                )
                if not r.get("success"):
                    raise RuntimeError(f"TTS init failed: {r.get('error')}")
                self.ready = True
            r = await loop.run_in_executor(None, self._cmd, {"cmd": "generate", "text": text})
            if not r.get("success"):
                raise RuntimeError(f"TTS generate failed: {r.get('error')}")
            pcm = base64.b64decode(r["audio"])
            return _pcm_to_wav(pcm, TTS_SAMPLE_RATE)


def _pcm_to_wav(pcm_s16le: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_s16le)
    return buf.getvalue()


_kokoro = _KokoroClient(TTS_MODEL, TTS_VOICE)


# --- STT ---------------------------------------------------------------------
def _to_wav16k_mono(src_bytes: bytes, suffix: str) -> str:
    """Write upload to temp, transcode to 16k mono s16le wav via ffmpeg, return path."""
    src = tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".bin")
    src.write(src_bytes)
    src.close()
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    out.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", src.name, "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", out.name],
        check=True, capture_output=True,
    )
    os.unlink(src.name)
    return out.name


def _transcribe_sync(wav_path: str) -> str:
    if STT_ENGINE == "parakeet":
        from parakeet_mlx import from_pretrained
        model = from_pretrained(PARAKEET_MODEL)
        res = model.transcribe(wav_path)
        return (getattr(res, "text", None) or str(res)).strip()
    import mlx_whisper
    res = mlx_whisper.transcribe(wav_path, path_or_hf_repo=WHISPER_MODEL)
    return (res.get("text", "") if isinstance(res, dict) else str(res)).strip()


# --- LLM ---------------------------------------------------------------------
def _llm_chat_sync(prompt: str) -> dict:
    with urllib.request.urlopen(LLM_BASE_URL.rstrip("/") + "/models", timeout=10) as r:
        served = [m.get("id") for m in json.loads(r.read().decode()).get("data", [])]
    use_model = LLM_MODEL if LLM_MODEL in served else (served[0] if served else LLM_MODEL)
    payload = json.dumps({
        "model": use_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512, "temperature": 0.7,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(
        LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=payload, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    msg = resp["choices"][0]["message"]
    reply = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    return {"reply": reply, "model": use_model, "served_models": served}


# --- Routes ------------------------------------------------------------------
# --- Config read/write (UI settings) -----------------------------------------
from config import load_config, save_config


@app.get("/api/config")
async def get_config():
    """Return the current config plus option lists for dropdowns."""
    return {
        "config": load_config(),
        "options": {
            "stt_engine": ["whisper", "parakeet"],
            "tts_voice": [
                "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
                "am_adam", "am_michael", "bf_emma", "bf_isabella",
                "bm_george", "bm_lewis",
            ],
        },
    }


@app.post("/api/config")
async def post_config(body: dict):
    """Persist edited config to config.yaml. Agent must restart to apply."""
    try:
        new = save_config(body or {})
        global STT_ENGINE, WHISPER_MODEL, PARAKEET_MODEL, LLM_MODEL, LLM_BASE_URL
        global TTS_MODEL, TTS_VOICE, TTS_SAMPLE_RATE
        STT_ENGINE = new["stt"]["engine"]
        WHISPER_MODEL = new["stt"]["whisper_model"]
        PARAKEET_MODEL = new["stt"]["parakeet_model"]
        LLM_MODEL = new["llm"]["model"]
        LLM_BASE_URL = new["llm"]["base_url"]
        TTS_MODEL = new["tts"]["model"]
        TTS_VOICE = new["tts"]["voice"]
        TTS_SAMPLE_RATE = int(new["tts"]["sample_rate"])
        return {"ok": True, "config": new,
                "note": "Saved. Restart the voice agent (bot.py) for STT/LLM/TTS/VAD changes to take effect."}
    except Exception as e:
        logger.exception("save config failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/api/health")
async def health():
    out = {"stt_engine": STT_ENGINE,
           "whisper_model": WHISPER_MODEL,
           "llm_model": LLM_MODEL,
           "llm_base_url": LLM_BASE_URL,
           "tts_model": TTS_MODEL,
           "ffmpeg": bool(shutil.which("ffmpeg"))}
    try:
        with urllib.request.urlopen(LLM_BASE_URL.rstrip("/") + "/models", timeout=5) as r:
            out["llm_reachable"] = r.status == 200
    except Exception as e:
        out["llm_reachable"] = False
        out["llm_error"] = str(e)
    return out


@app.post("/api/stt")
async def api_stt(audio: UploadFile = File(...)):
    t0 = time.perf_counter()
    try:
        data = await audio.read()
        suffix = Path(audio.filename or "").suffix or ".webm"
        wav_path = await asyncio.get_running_loop().run_in_executor(
            None, _to_wav16k_mono, data, suffix)
        try:
            text = await asyncio.get_running_loop().run_in_executor(
                None, _transcribe_sync, wav_path)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
        return {"ok": True, "text": text,
                "engine": STT_ENGINE,
                "model": WHISPER_MODEL if STT_ENGINE != "parakeet" else PARAKEET_MODEL,
                "wall_s": round(time.perf_counter() - t0, 2)}
    except subprocess.CalledProcessError as e:
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": f"ffmpeg failed: {e.stderr.decode()[-300:]}"})
    except Exception as e:
        logger.exception("STT failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# --- Realtime STT over WebSocket --------------------------------------------
import numpy as np


def _transcribe_np_sync(pcm_f32: "np.ndarray") -> str:
    """Transcribe a float32 mono 16k numpy array with Whisper MLX (no ffmpeg)."""
    if STT_ENGINE == "parakeet":
        from parakeet_mlx import from_pretrained
        global _pk_model
        try:
            _pk_model
        except NameError:
            _pk_model = from_pretrained(PARAKEET_MODEL)
        res = _pk_model.transcribe(pcm_f32, 16000)
        return (getattr(res, "text", None) or str(res)).strip()
    import mlx_whisper
    res = mlx_whisper.transcribe(pcm_f32, path_or_hf_repo=WHISPER_MODEL)
    return (res.get("text", "") if isinstance(res, dict) else str(res)).strip()


@app.websocket("/ws/stt")
async def ws_stt(ws: WebSocket):
    """Realtime STT.

    Protocol (browser -> server):
      - binary frames: raw PCM16 mono @16kHz (little-endian int16)
      - text 'reset'  : clear the buffer (start a new utterance)
      - text 'final'  : force a final transcription now
    Server -> browser (JSON text):
      {"type":"partial","text":...}  rolling interim transcript
      {"type":"final","text":...}    final transcript for the utterance
    """
    await ws.accept()
    SR = 16000
    buf = np.zeros(0, dtype=np.float32)
    last_transcribe = 0.0
    INTERVAL = 0.7          # transcribe at most every 0.7s
    MAX_WINDOW = SR * 20    # cap rolling buffer at 20s to bound latency/mem
    loop = asyncio.get_running_loop()
    busy = False

    async def transcribe_and_send(kind: str):
        nonlocal busy
        if busy or buf.size < SR * 0.3:   # need >=0.3s of audio
            if kind == "final" and buf.size:
                pass
            else:
                return
        busy = True
        try:
            snapshot = buf.copy()
            text = await loop.run_in_executor(None, _transcribe_np_sync, snapshot)
            await ws.send_json({"type": kind, "text": text})
        except Exception as e:
            await ws.send_json({"type": "error", "text": str(e)})
        finally:
            busy = False

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"] is not None:
                pcm16 = np.frombuffer(msg["bytes"], dtype=np.int16)
                chunk = pcm16.astype(np.float32) / 32768.0
                buf = np.concatenate([buf, chunk])
                if buf.size > MAX_WINDOW:
                    buf = buf[-MAX_WINDOW:]
                now = time.perf_counter()
                if now - last_transcribe >= INTERVAL and not busy:
                    last_transcribe = now
                    asyncio.create_task(transcribe_and_send("partial"))
            elif "text" in msg and msg["text"] is not None:
                cmd = msg["text"].strip()
                if cmd == "reset":
                    buf = np.zeros(0, dtype=np.float32)
                elif cmd == "final":
                    await transcribe_and_send("final")
                    buf = np.zeros(0, dtype=np.float32)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("ws_stt error")
        try:
            await ws.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass


@app.post("/api/llm")
async def api_llm(body: dict):
    t0 = time.perf_counter()
    prompt = (body or {}).get("prompt", "").strip()
    if not prompt:
        return JSONResponse(status_code=400, content={"ok": False, "error": "empty prompt"})
    try:
        r = await asyncio.get_running_loop().run_in_executor(None, _llm_chat_sync, prompt)
        r.update({"ok": True, "wall_s": round(time.perf_counter() - t0, 2)})
        return r
    except Exception as e:
        logger.exception("LLM failed")
        return JSONResponse(status_code=500,
                            content={"ok": False, "error": f"{e} (is the LLM server on {LLM_BASE_URL} running?)"})


@app.post("/api/tts")
async def api_tts(body: dict):
    text = (body or {}).get("text", "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"ok": False, "error": "empty text"})
    try:
        wav = await _kokoro.synth(text)
        return Response(content=wav, media_type="audio/wav")
    except Exception as e:
        logger.exception("TTS failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(INDEX_HTML)


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Jarvis Component Tester</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#0b0d10; color:#e6e9ef; }
  header { padding:18px 22px; border-bottom:1px solid #1e242c; display:flex; align-items:center; gap:12px; }
  header h1 { font-size:18px; margin:0; font-weight:600; }
  #health { font-size:12px; color:#8b93a1; margin-left:auto; white-space:pre; }
  main { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:18px; padding:22px; }
  .card { background:#12161c; border:1px solid #1e242c; border-radius:12px; padding:18px; display:flex; flex-direction:column; gap:12px; }
  .card h2 { margin:0; font-size:15px; letter-spacing:.02em; }
  .tag { font-size:11px; color:#6b7280; font-weight:400; }
  textarea, input[type=text] { width:100%; background:#0b0d10; border:1px solid #262d36; color:#e6e9ef;
         border-radius:8px; padding:10px; font:inherit; resize:vertical; }
  textarea { min-height:70px; }
  button { background:#3b82f6; color:#fff; border:0; border-radius:8px; padding:9px 14px; font:inherit;
           font-weight:600; cursor:pointer; }
  button.secondary { background:#242b34; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  .out { background:#0b0d10; border:1px solid #1e242c; border-radius:8px; padding:10px; min-height:38px;
         white-space:pre-wrap; word-break:break-word; font-size:14px; }
  .meta { font-size:12px; color:#6b7280; }
  .err { color:#f87171; }
  .ok  { color:#34d399; }
  .rec { background:#ef4444; }
  audio { width:100%; }
  label.file { display:inline-block; }
</style>
</head>
<body>
<header>
  <h1>Jarvis Component Tester</h1>
  <div id="health">loading config…</div>
</header>
<main>
  <!-- SETTINGS -->
  <section class="card" style="grid-column:1/-1;">
    <h2>Settings <span class="tag">edit STT · LLM · TTS · VAD</span>
      <button id="cfg-toggle" class="secondary" style="margin-left:auto;padding:4px 10px;">Show/Hide</button>
    </h2>
    <div id="cfg-body">
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;">
        <div>
          <div class="tag">STT</div>
          <label class="meta">Engine</label>
          <select id="c-stt-engine"></select>
          <label class="meta">Whisper model</label>
          <input type="text" id="c-stt-whisper"/>
          <label class="meta">Parakeet model</label>
          <input type="text" id="c-stt-parakeet"/>
        </div>
        <div>
          <div class="tag">LLM</div>
          <label class="meta">Model</label>
          <input type="text" id="c-llm-model"/>
          <label class="meta">Base URL</label>
          <input type="text" id="c-llm-url"/>
          <label class="meta">Max tokens</label>
          <input type="text" id="c-llm-maxtok"/>
        </div>
        <div>
          <div class="tag">TTS</div>
          <label class="meta">Model</label>
          <input type="text" id="c-tts-model"/>
          <label class="meta">Voice</label>
          <select id="c-tts-voice"></select>
          <label class="meta">Sample rate</label>
          <input type="text" id="c-tts-sr"/>
        </div>
        <div>
          <div class="tag">VAD</div>
          <label class="meta">Confidence <span id="v-conf"></span></label>
          <input type="range" id="c-vad-conf" min="0" max="1" step="0.05"/>
          <label class="meta">Min volume <span id="v-vol"></span></label>
          <input type="range" id="c-vad-vol" min="0" max="1" step="0.05"/>
          <label class="meta">Start secs <span id="v-start"></span></label>
          <input type="range" id="c-vad-start" min="0" max="1" step="0.05"/>
          <label class="meta">Stop secs <span id="v-stop"></span></label>
          <input type="range" id="c-vad-stop" min="0" max="2" step="0.1"/>
          <label class="meta" style="display:flex;gap:8px;align-items:center;margin-top:8px;">
            <input type="checkbox" id="c-turn-smart" style="width:auto;"/> Smart turn detection
          </label>
        </div>
      </div>
      <div style="margin-top:14px;">
        <div class="tag">LLM system prompt</div>
        <textarea id="c-llm-prompt" style="min-height:90px;"></textarea>
      </div>
      <div class="row" style="margin-top:12px;">
        <button id="cfg-save">Save settings</button>
        <button id="cfg-reload" class="secondary">Reload from file</button>
        <span class="meta" id="cfg-status"></span>
      </div>
    </div>
  </section>

  <!-- STT -->
  <section class="card">
    <h2>STT <span class="tag" id="stt-tag"></span></h2>
    <div class="row">
      <button id="stt-rec">● Record</button>
      <button id="stt-stop" class="secondary" disabled>■ Stop</button>
      <button id="stt-live" class="secondary">◉ Live STT</button>
      <label class="file secondary" style="padding:9px 14px;border-radius:8px;background:#242b34;cursor:pointer;">
        Upload WAV/audio
        <input id="stt-file" type="file" accept="audio/*" hidden/>
      </label>
    </div>
    <audio id="stt-audio" controls hidden></audio>
    <div class="meta" id="stt-status">Idle. Record, go Live, or upload a file.</div>
    <div class="out" id="stt-out">—</div>
  </section>

  <!-- LLM -->
  <section class="card">
    <h2>LLM <span class="tag" id="llm-tag"></span></h2>
    <textarea id="llm-prompt" placeholder="Type a prompt…">Say hello in one short sentence.</textarea>
    <div class="row"><button id="llm-send">Send</button></div>
    <div class="meta" id="llm-status">Idle.</div>
    <div class="out" id="llm-out">—</div>
  </section>

  <!-- TTS -->
  <section class="card">
    <h2>TTS <span class="tag" id="tts-tag"></span></h2>
    <textarea id="tts-text" placeholder="Text to synthesize…">Hello, I'm Jarvis. This is a test of the text to speech component.</textarea>
    <div class="row"><button id="tts-say">Synthesize</button></div>
    <div class="meta" id="tts-status">Idle.</div>
    <audio id="tts-audio" controls hidden></audio>
  </section>
</main>

<script>
const $ = (id) => document.getElementById(id);

// ---- Settings ----
$('cfg-toggle').onclick = () => {
  const b = $('cfg-body'); b.style.display = b.style.display === 'none' ? '' : 'none';
};
function fillSelect(el, opts, val) {
  el.innerHTML = '';
  const seen = new Set();
  [val, ...opts].forEach(o => {
    if (o == null || seen.has(o)) return; seen.add(o);
    const opt = document.createElement('option'); opt.value = o; opt.textContent = o; el.appendChild(opt);
  });
  el.value = val;
}
function bindRange(id, labelId) {
  const upd = () => $(labelId).textContent = $(id).value;
  $(id).oninput = upd; upd();
}
async function loadConfig() {
  const r = await fetch('/api/config'); const j = await r.json();
  const c = j.config, o = j.options;
  fillSelect($('c-stt-engine'), o.stt_engine, c.stt.engine);
  $('c-stt-whisper').value = c.stt.whisper_model;
  $('c-stt-parakeet').value = c.stt.parakeet_model;
  $('c-llm-model').value = c.llm.model;
  $('c-llm-url').value = c.llm.base_url;
  $('c-llm-maxtok').value = c.llm.max_tokens;
  $('c-llm-prompt').value = c.llm.system_prompt;
  $('c-tts-model').value = c.tts.model;
  fillSelect($('c-tts-voice'), o.tts_voice, c.tts.voice);
  $('c-tts-sr').value = c.tts.sample_rate;
  $('c-vad-conf').value = c.vad.confidence;
  $('c-vad-vol').value = c.vad.min_volume;
  $('c-vad-start').value = c.vad.start_secs;
  $('c-vad-stop').value = c.vad.stop_secs;
  $('c-turn-smart').checked = !!c.turn.smart_turn;
  ['c-vad-conf','v-conf','c-vad-vol','v-vol','c-vad-start','v-start','c-vad-stop','v-stop'];
  bindRange('c-vad-conf','v-conf'); bindRange('c-vad-vol','v-vol');
  bindRange('c-vad-start','v-start'); bindRange('c-vad-stop','v-stop');
}
$('cfg-reload').onclick = () => { loadConfig(); $('cfg-status').textContent = 'reloaded from file'; };
$('cfg-save').onclick = async () => {
  const body = {
    stt: { engine: $('c-stt-engine').value, whisper_model: $('c-stt-whisper').value, parakeet_model: $('c-stt-parakeet').value },
    llm: { model: $('c-llm-model').value, base_url: $('c-llm-url').value, max_tokens: Number($('c-llm-maxtok').value)||4096, system_prompt: $('c-llm-prompt').value },
    tts: { model: $('c-tts-model').value, voice: $('c-tts-voice').value, sample_rate: Number($('c-tts-sr').value)||24000 },
    vad: { confidence: Number($('c-vad-conf').value), min_volume: Number($('c-vad-vol').value), start_secs: Number($('c-vad-start').value), stop_secs: Number($('c-vad-stop').value) },
    turn: { smart_turn: $('c-turn-smart').checked },
  };
  $('cfg-save').disabled = true; $('cfg-status').textContent = 'saving…';
  try {
    const r = await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error||'failed');
    $('cfg-status').innerHTML = '<span class="ok">✓ saved</span> — restart the voice agent to apply';
  } catch(e) { $('cfg-status').innerHTML = '<span class="err">✗ '+e.message+'</span>'; }
  finally { $('cfg-save').disabled = false; }
};
loadConfig();

// ---- health ----
fetch('/api/health').then(r=>r.json()).then(h=>{
  $('health').textContent =
    `STT: ${h.stt_engine} (${(h.whisper_model||'').split('/').pop()})\n` +
    `LLM: ${(h.llm_model||'').split('/').pop()} ${h.llm_reachable ? '● up' : '○ down'}\n` +
    `TTS: ${(h.tts_model||'').split('/').pop()}`;
  $('stt-tag').textContent = h.stt_engine;
  $('llm-tag').textContent = h.llm_reachable ? 'server up' : 'server DOWN';
  $('llm-tag').className = 'tag ' + (h.llm_reachable ? 'ok' : 'err');
  $('tts-tag').textContent = (h.tts_model||'').split('/').pop();
}).catch(e=> $('health').textContent = 'health check failed: '+e);

// ---- STT: record ----
let mediaRecorder, chunks = [];
$('stt-rec').onclick = async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    mediaRecorder = new MediaRecorder(stream);
    chunks = [];
    mediaRecorder.ondataavailable = e => chunks.push(e.data);
    mediaRecorder.onstop = () => {
      const blob = new Blob(chunks, {type: mediaRecorder.mimeType || 'audio/webm'});
      const url = URL.createObjectURL(blob);
      const a = $('stt-audio'); a.src = url; a.hidden = false;
      sttSend(blob, 'recording' + (blob.type.includes('webm') ? '.webm' : '.ogg'));
      stream.getTracks().forEach(t=>t.stop());
    };
    mediaRecorder.start();
    $('stt-rec').disabled = true; $('stt-stop').disabled = false;
    $('stt-rec').classList.add('rec');
    $('stt-status').textContent = 'Recording… speak, then Stop.';
  } catch(e) { $('stt-status').innerHTML = '<span class="err">mic error: '+e+'</span>'; }
};
$('stt-stop').onclick = () => {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
  $('stt-rec').disabled = false; $('stt-stop').disabled = true;
  $('stt-rec').classList.remove('rec');
};
$('stt-file').onchange = (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const a = $('stt-audio'); a.src = URL.createObjectURL(f); a.hidden = false;
  sttSend(f, f.name);
};
async function sttSend(blob, filename) {
  $('stt-status').textContent = 'Transcribing…';
  $('stt-out').textContent = '—';
  const fd = new FormData(); fd.append('audio', blob, filename);
  const t0 = performance.now();
  try {
    const r = await fetch('/api/stt', {method:'POST', body: fd});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'failed');
    $('stt-out').textContent = j.text || '(empty transcription)';
    $('stt-status').innerHTML = `<span class="ok">✓</span> ${j.model} · ${j.wall_s}s`;
  } catch(e) {
    $('stt-status').innerHTML = '<span class="err">✗ '+e.message+'</span>';
  }
}

// ---- STT: LIVE (realtime over WebSocket) ----
let liveCtx, liveStream, liveNode, liveSource, liveWS, liveOn = false;
function downsampleTo16k(f32, inRate) {
  if (inRate === 16000) return f32;
  const ratio = inRate / 16000;
  const outLen = Math.floor(f32.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) out[i] = f32[Math.floor(i * ratio)];
  return out;
}
function f32ToPCM16(f32) {
  const buf = new ArrayBuffer(f32.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < f32.length; i++) {
    let s = Math.max(-1, Math.min(1, f32[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}
$('stt-live').onclick = async () => {
  if (liveOn) { stopLive(); return; }
  try {
    liveStream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1, echoCancellation:true, noiseSuppression:true}});
    liveCtx = new (window.AudioContext || window.webkitAudioContext)();
    const inRate = liveCtx.sampleRate;
    liveSource = liveCtx.createMediaStreamSource(liveStream);
    liveNode = liveCtx.createScriptProcessor(4096, 1, 1);
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    liveWS = new WebSocket(`${proto}://${location.host}/ws/stt`);
    liveWS.binaryType = 'arraybuffer';
    liveWS.onopen = () => liveWS.send('reset');
    liveWS.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'error') { $('stt-status').innerHTML = '<span class="err">✗ '+m.text+'</span>'; return; }
      $('stt-out').textContent = m.text || '…';
      if (m.type === 'partial') $('stt-status').innerHTML = '<span class="ok">◉ live</span> listening…';
      if (m.type === 'final')   $('stt-status').innerHTML = '<span class="ok">✓</span> final';
    };
    liveWS.onerror = () => $('stt-status').innerHTML = '<span class="err">✗ websocket error</span>';
    liveNode.onaudioprocess = (e) => {
      if (!liveWS || liveWS.readyState !== 1) return;
      const input = e.inputBuffer.getChannelData(0);
      const ds = downsampleTo16k(input, inRate);
      liveWS.send(f32ToPCM16(ds));
    };
    liveSource.connect(liveNode);
    liveNode.connect(liveCtx.destination);
    liveOn = true;
    $('stt-live').textContent = '■ Stop Live';
    $('stt-live').classList.remove('secondary'); $('stt-live').classList.add('rec');
    $('stt-out').textContent = '…';
    $('stt-status').innerHTML = '<span class="ok">◉ live</span> speak now…';
  } catch(e) {
    $('stt-status').innerHTML = '<span class="err">live error: '+e+'</span>';
    stopLive();
  }
};
function stopLive() {
  liveOn = false;
  $('stt-live').textContent = '◉ Live STT';
  $('stt-live').classList.add('secondary'); $('stt-live').classList.remove('rec');
  try { if (liveWS && liveWS.readyState === 1) { liveWS.send('final'); } } catch(_) {}
  try { if (liveNode) liveNode.disconnect(); } catch(_) {}
  try { if (liveSource) liveSource.disconnect(); } catch(_) {}
  try { if (liveStream) liveStream.getTracks().forEach(t=>t.stop()); } catch(_) {}
  try { if (liveCtx) liveCtx.close(); } catch(_) {}
  setTimeout(()=>{ try { if (liveWS) liveWS.close(); } catch(_){} }, 500);
}

// ---- LLM ----
$('llm-send').onclick = async () => {
  const prompt = $('llm-prompt').value.trim();
  if (!prompt) return;
  $('llm-status').textContent = 'Thinking…'; $('llm-out').textContent = '—';
  $('llm-send').disabled = true;
  try {
    const r = await fetch('/api/llm', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({prompt})});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'failed');
    $('llm-out').textContent = j.reply || '(empty reply)';
    $('llm-status').innerHTML = `<span class="ok">✓</span> ${j.model} · ${j.wall_s}s`;
  } catch(e) {
    $('llm-status').innerHTML = '<span class="err">✗ '+e.message+'</span>';
  } finally { $('llm-send').disabled = false; }
};

// ---- TTS ----
$('tts-say').onclick = async () => {
  const text = $('tts-text').value.trim();
  if (!text) return;
  $('tts-status').textContent = 'Synthesizing…';
  $('tts-say').disabled = true;
  const t0 = performance.now();
  try {
    const r = await fetch('/api/tts', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text})});
    if (!r.ok) { const j = await r.json().catch(()=>({})); throw new Error(j.error || ('HTTP '+r.status)); }
    const blob = await r.blob();
    const a = $('tts-audio'); a.src = URL.createObjectURL(blob); a.hidden = false;
    await a.play().catch(()=>{});
    $('tts-status').innerHTML = `<span class="ok">✓</span> ${((performance.now()-t0)/1000).toFixed(2)}s · ${(blob.size/1024|0)} KB`;
  } catch(e) {
    $('tts-status').innerHTML = '<span class="err">✗ '+e.message+'</span>';
  } finally { $('tts-say').disabled = false; }
};
</script>
</body>
</html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jarvis component tester")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    logger.info(f"Component tester on http://{args.host}:{args.port}  "
                f"(STT={STT_ENGINE}, LLM={LLM_MODEL}, TTS={TTS_MODEL})")
    uvicorn.run(app, host=args.host, port=args.port)
