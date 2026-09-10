# Jarvis — Local voice agents on macOS with Pipecat

![screenshot](assets/debug-console-screenshot.png)

Pipecat is an open-source, vendor-neutral framework for building real-time voice (and video) AI applications.

This repository contains a voice agent ("Jarvis") that runs entirely with **local models on macOS**. On an M-series Mac, you can achieve voice-to-voice latency of <800 ms with relatively strong models.

The [server/bot.py](server/bot.py) pipeline uses these models by default:

- **VAD** — Silero VAD
- **Turn detection** — smart-turn v2
- **STT** — MLX Whisper (or Parakeet-MLX)
- **LLM** — an OpenAI-compatible local server (e.g. Qwen3 / Gemma3n via LM Studio)
- **TTS** — Kokoro (or Marvis) via [mlx-audio](https://github.com/Blaizzy/mlx-audio)

Any of these can be swapped out or reconfigured. All models and settings are driven by [`server/config.yaml`](server/config.yaml) (overridable via environment variables — see [`server/config.py`](server/config.py)).

The bot and web client communicate over a low-latency, local, serverless WebRTC connection. See the Pipecat [SmallWebRTCTransport docs](https://docs.pipecat.ai/server/services/transport/small-webrtc) for details.

## Project structure

```
macos-local-voice-agents/
├── README.md                     # This file
├── .gitignore
│
├── assets/                       # Static assets used by docs
│   └── debug-console-screenshot.png
│
├── server/                       # Python voice-agent backend (Pipecat + MLX)
│   ├── bot.py                    # Main entry point — builds & runs the WebRTC voice pipeline
│   ├── bot.py.orig               # Original/reference version of bot.py
│   ├── config.py                 # Central config loader (env var > config.yaml > defaults)
│   ├── config.yaml               # Model & pipeline configuration (STT/LLM/TTS/VAD/turn)
│   │
│   ├── parakeet_stt.py           # Parakeet-MLX STT service (Pipecat SegmentedSTTService wrapper)
│   ├── tts_mlx_isolated.py       # Process-isolated MLX TTS service (avoids Metal threading conflicts)
│   ├── kokoro_worker.py          # Standalone Kokoro TTS worker process (JSON over stdin/stdout)
│   ├── marvis_worker.py          # Standalone Marvis TTS worker process (JSON over stdin/stdout)
│   │
│   ├── component_test_server.py  # Standalone web console to test STT/LLM/TTS independently
│   │
│   ├── pyproject.toml            # Project metadata & dependencies (uv)
│   ├── uv.lock                   # uv lockfile
│   ├── requirements.txt          # pip dependencies (alternative to uv)
│   ├── env.example               # Example environment file (API keys, optional)
│   ├── .gitignore
│   └── .venv/                    # Local virtual environment (generated, not committed)
│
└── client/                       # Next.js / React web client (voice-ui-kit debug console)
    ├── src/
    │   └── app/
    │       ├── page.tsx          # Root page — renders the Pipecat ConsoleTemplate (smallwebrtc)
    │       ├── layout.tsx        # App layout
    │       └── favicon.ico
    ├── package.json              # Client dependencies & scripts (dev/build/start/lint)
    ├── package-lock.json
    ├── next.config.ts            # Next.js configuration
    ├── tsconfig.json             # TypeScript configuration
    ├── eslint.config.mjs         # ESLint configuration
    ├── next-env.d.ts
    ├── .gitignore
    └── .next/                    # Next.js build output (generated, not committed)
```

## Models and dependencies

Silero VAD, smart-turn, MLX Whisper, and the MLX TTS models run locally. On first startup, the agent downloads any model weights that aren't already cached, so the first run can take 30+ seconds. Subsequent startups are much faster.

The LLM service uses the OpenAI-compatible chat completion HTTP API, so you need to run a local OpenAI-compatible LLM server. An easy, high-performance option on macOS is [LM Studio](https://lmstudio.ai/) — open the **Developer** tab and start an HTTP server (default `http://127.0.0.1:1234/v1`, matching `config.yaml`).

To warm the TTS voice model cache before the first bot run:

```shell
mlx-audio.generate --model "mlx-community/Kokoro-82M-bf16" --text "Hello, I'm Jarvis!" --output "output.wav"
# or
mlx-audio.generate --model "Marvis-AI/marvis-tts-250m-v0.1" --text "Hello, I'm Jarvis!" --output "output.wav"
```

## Run the voice agent (server)

```shell
cd server/
```

Using **uv**:

```shell
uv run bot.py
```

Using **pip**:

```shell
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

After the first run (with all models cached), set `HF_HUB_OFFLINE=1` to skip network model-update checks and speed up startup:

```shell
HF_HUB_OFFLINE=1 uv run bot.py
```

### Configuration

Edit [`server/config.yaml`](server/config.yaml) to change models and pipeline behavior (STT engine, LLM model/URL, TTS model/voice, VAD thresholds, smart-turn). Every value is overridable via environment variables (e.g. `STT_ENGINE`, `LLM_MODEL`, `LLM_BASE_URL`, `TTS_MODEL`, `TTS_VOICE`) — see the docstring in [`server/config.py`](server/config.py).

### Testing components independently

To exercise STT, LLM, and TTS separately (without WebRTC/Pipecat), run the component test server:

```shell
cd server/
.venv/bin/python component_test_server.py --port 8080
open http://localhost:8080
```

## Start the web client

The web client is a Next.js/React app based on [voice-ui-kit](https://github.com/pipecat-ai/voice-ui-kit), using its standard debug console template. It connects to the local agent over serverless WebRTC.

```shell
cd client/
npm i
npm run dev
# Navigate to the URL shown in the terminal in your web browser
```

## Further reading

For a deep dive into voice AI — network transport, latency optimization, and designing tool calling and complex workflows — see the [Voice AI & Voice Agents Illustrated Guide](https://voiceaiandvoiceagents.com/).
