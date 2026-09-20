# Jarvis system-wide dictation (macOS alpha)

This process is separate from the Pipecat/SmallWebRTC voice agent. It captures
the Mac microphone locally, transcribes with Parakeet-MLX, optionally cleans the
English transcript with a local s1-mini GGUF, then inserts it with
pasteboard snapshot → Cmd+V → restore.

## Install and run

Requirements: Apple Silicon, macOS, Python 3.12, and PortAudio.

```bash
brew install python@3.12 portaudio
WITH_PARAKEET=1 ./scripts/setup_server.sh
./scripts/run_dictation.sh
```

On first use, macOS may prompt separately for:

1. **Microphone** — audio capture.
2. **Accessibility** — synthetic Cmd+V insertion.
3. **Input Monitoring** — the global event tap.

Enable the terminal (and, if shown, its Python executable) in **System Settings
→ Privacy & Security** under each category, quit the process, and launch it
again. The menu-bar `J` changes to `J ●` while recording, `J …` while
transcribing, and `J !` on an error.

Hold Right Command, speak, then release. Press Escape while recording to cancel.
If Accessibility is unavailable or Cmd+V dispatch fails, Jarvis leaves the
transcript on the clipboard and prints a prompt to press Cmd+V. The microphone
is closed if hotkey health is lost for 1.5 seconds.

The first Parakeet run downloads model weights. After that, use
`HF_HUB_OFFLINE=1 ./scripts/run_dictation.sh` to enforce cached/offline loading.

## Configuration

Edit the `dictation` section in `server/config.yaml`:

```yaml
dictation:
  hotkey: right_command       # left_command or a chord such as control+space
  stt_backend: parakeet       # local-only MVP backend
  stt_model: sonic-speech/parakeet-tdt-0.6b-v3-int8
  language: en
  cleanup_enabled: false
  cleanup_backend: s1-mini
  cleanup_model_path: ""
  sample_rate: 16000
  fail_closed_seconds: 1.5
  clipboard_restore_delay_ms: 150
```

Every key has a namespaced environment override, such as
`DICTATION_HOTKEY`, `DICTATION_STT_BACKEND`, `DICTATION_STT_MODEL`,
`DICTATION_CLEANUP_ENABLED`, and `DICTATION_CLEANUP_MODEL_PATH`. These do not
change the voice-agent `STT_ENGINE` or hotkey settings.

### Optional s1-mini cleanup

Cleanup is English-only and off by default. Install the local llama.cpp binding,
download an Apache-2.0 s1-mini Q4 GGUF, and point the configuration at it:

```bash
WITH_PARAKEET=1 WITH_CLEANUP=1 ./scripts/setup_server.sh
# Install `hf`, then:
hf download superwhisper/s1-mini-GGUF \
  --include "*Q4_K_M*.gguf" --local-dir "$HOME/Models/s1-mini"

DICTATION_CLEANUP_ENABLED=true \
DICTATION_CLEANUP_MODEL_PATH="$HOME/Models/s1-mini/<downloaded-file>.gguf" \
./scripts/run_dictation.sh
```

No audio or transcript is sent to a cloud service. Do not use non-commercial
GEC weights in this product path.

## Linux/CI validation and Mac smoke test

Pure configuration, state-machine, hotkey-parser, and clipboard transaction
tests run anywhere:

```bash
cd server
python -m unittest discover -s tests -v
```

The Quartz event tap, TCC permissions, microphone, MLX inference, and target-app
paste behavior require a Mac. Verify Notes, Slack, Safari, Cursor, and Terminal;
also verify Escape cancellation, clipboard restoration, permission-denied
copy-only fallback, and terminating the listener while held.

