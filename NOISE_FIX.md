# Noise Suppression

## RNNoise (OSS) Input Filter

The Jarvis voice agent now supports fully open-source RNNoise mic noise suppression via Pipecat's `RNNoiseFilter`. This denoises audio on the server **before** VAD and STT processing.

### Enable/Disable

Edit `server/config.yaml`:

```yaml
audio:
  noise_filter: rnnoise  # or 'none' to disable
```

Or set the environment variable:

```bash
export AUDIO_NOISE_FILTER=rnnoise  # or 'none'
```

### Installation

Install the RNNoise extra for pipecat:

```bash
pip install "pipecat-ai[rnnoise]"
```

This pulls in `pyrnnoise` and `soxr` dependencies.

### Smoke Test

Run the included smoke test to verify the filter works:

```bash
cd server
python test_rnnoise_filter.py
```

Expected output:
```
sample_rate=16000
input_bytes=32000 output_bytes=32000
input_rms=7458.8 output_rms=5234.2
rms_ratio=0.702
PASS: RNNoiseFilter processed PCM successfully
```

### Important Notes

**Do not stack multiple noise suppression paths:**
- Pick **one** of: RNNoise (server-side) OR browser/OS Voice Isolation OR Krisp
- Stacking multiple NS solutions can cause audio quality degradation
- Echo cancellation should remain enabled regardless of NS choice

### Configuration Guidance

If ambient sound triggers turns before you speak:
1. Tighten VAD thresholds in `server/config.yaml`:
   - Increase `vad.min_volume` (e.g., 0.55)
   - Increase `vad.confidence` (e.g., 0.7)
   - Increase `vad.start_secs` (e.g., 0.35)
2. Enable **one** noise suppression method (RNNoise recommended)
3. Keep echo cancellation enabled

### Technical Details

- **When**: Audio is filtered immediately after mic capture, before VAD/STT
- **Where**: `TransportParams.audio_in_filter` in `SmallWebRTCTransport`
- **How**: RNNoiseFilter wraps the pyrnnoise library (recurrent neural network trained on speech/noise)
- **Compatibility**: Includes `rnnoise_compat.py` shim for pyrnnoise 0.4.x + audiolab 0.5+ API drift
