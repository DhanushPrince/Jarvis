# Jarvis evals

The default suite is a fully local, text-only smoke test. It starts
`server/bot_eval.py` with Pipecat's official eval transport, asks for the capital
of Germany, and checks the response contains `Berlin`. It uses no judge, cloud
API, STT, TTS, GPU, or Apple MLX model.

```sh
python3 -m venv .venv
.venv/bin/pip install -r evals/requirements.txt
evals/run.sh
```

To start the bot transport by itself:

```sh
.venv/bin/python server/bot_eval.py -t eval --port 7860
```

## Running Jarvis's configured LLM

The smoke responder proves the runner, eval transport, pipeline, and harness
integration deterministically. To evaluate the real LLM, start the
OpenAI-compatible local server configured in `server/config.yaml`, then run:

```sh
JARVIS_EVAL_LLM=openai evals/run.sh
```

This mode still uses the same system prompt, model, base URL, API key, and token
limit as Jarvis. The default URL is local (`http://127.0.0.1:1234/v1`).

## Text mode vs audio mode

Text mode intentionally omits STT, TTS, VAD input, PTT, and RNNoise. It runs on
Linux CPU CI and macOS without loading Jarvis's Apple MLX Whisper/Kokoro
services. Pipecat's context aggregator initializes its small CPU turn model, but
text input does not traverse the audio turn path. The existing `server/bot.py`
remains the full macOS SmallWebRTC voice path and continues to use its configured
VAD and turn settings.

For audio evals, install `pipecat-ai[evals]` (already included above) and add a
separate audio pipeline using Pipecat's CPU-friendly Moonshine STT and Kokoro
ONNX TTS. Audio scenarios can then set `user.modality: audio` and
`judge.modality: audio`. PTT and RNNoise should remain explicit opt-ins for that
pipeline; they are not meaningful in the text smoke.
