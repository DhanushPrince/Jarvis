"""Central config loader for the Jarvis voice agent.

Loads config.yaml once and exposes a nested dict. Precedence for each value:
    environment variable  >  config.yaml  >  built-in default.

Env var names (all optional):
    STT_ENGINE, WHISPER_MODEL, PARAKEET_MODEL,
    LLM_MODEL, LLM_BASE_URL, LLM_API_KEY, LLM_MAX_TOKENS, SYSTEM_PROMPT,
    TTS_MODEL, TTS_VOICE, TTS_SAMPLE_RATE,
    VAD_CONFIDENCE, VAD_START_SECS, VAD_STOP_SECS, VAD_MIN_VOLUME,
    SMART_TURN, HOTKEY_ENABLED, HOTKEY_KEY, HOTKEY_SERVER_URL

Usage:
    from config import CONFIG
    CONFIG["stt"]["engine"], CONFIG["vad"]["min_volume"], ...
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from loguru import logger

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_DEFAULTS = {
    "stt": {
        "engine": "whisper",
        "whisper_model": "mlx-community/whisper-base-mlx",
        "parakeet_model": "sonic-speech/parakeet-tdt-0.6b-v3-int8",
    },
    "llm": {
        "model": "mlx-community/Qwen3-0.6B-4bit",
        "base_url": "http://127.0.0.1:1234/v1",
        "api_key": "dummyKey",
        "max_tokens": 4096,
        "system_prompt": 'You are Jarvis, a friendly, helpful voice assistant. '
        'Start the conversation by saying, "Hello, I\'m Jarvis!" Then stop and wait for the user.',
    },
    "tts": {
        "model": "mlx-community/Kokoro-82M-bf16",
        "voice": "af_heart",
        "sample_rate": 24000,
    },
    "vad": {
        "confidence": 0.5,
        "start_secs": 0.2,
        "stop_secs": 0.2,
        "min_volume": 0.3,
    },
    "turn": {
        "smart_turn": True,
    },
    "hotkey": {
        "enabled": False,
        "key": "right_command",
        "server_url": "http://127.0.0.1:7860",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _coerce(val: str):
    """Coerce an env string to bool/int/float when it looks like one."""
    low = val.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return val


# (section, key, ENV_NAME)
_ENV_MAP = [
    ("stt", "engine", "STT_ENGINE"),
    ("stt", "whisper_model", "WHISPER_MODEL"),
    ("stt", "parakeet_model", "PARAKEET_MODEL"),
    ("llm", "model", "LLM_MODEL"),
    ("llm", "base_url", "LLM_BASE_URL"),
    ("llm", "api_key", "LLM_API_KEY"),
    ("llm", "max_tokens", "LLM_MAX_TOKENS"),
    ("llm", "system_prompt", "SYSTEM_PROMPT"),
    ("tts", "model", "TTS_MODEL"),
    ("tts", "voice", "TTS_VOICE"),
    ("tts", "sample_rate", "TTS_SAMPLE_RATE"),
    ("vad", "confidence", "VAD_CONFIDENCE"),
    ("vad", "start_secs", "VAD_START_SECS"),
    ("vad", "stop_secs", "VAD_STOP_SECS"),
    ("vad", "min_volume", "VAD_MIN_VOLUME"),
    ("turn", "smart_turn", "SMART_TURN"),
    ("hotkey", "enabled", "HOTKEY_ENABLED"),
    ("hotkey", "key", "HOTKEY_KEY"),
    ("hotkey", "server_url", "HOTKEY_SERVER_URL"),
]


def load_config() -> dict:
    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}

    # 1) merge YAML
    if _CONFIG_PATH.exists():
        if yaml is None:
            logger.warning("PyYAML not installed; ignoring config.yaml")
        else:
            try:
                with open(_CONFIG_PATH) as f:
                    file_cfg = yaml.safe_load(f) or {}
                cfg = _deep_merge(cfg, file_cfg)
                logger.info(f"Loaded config from {_CONFIG_PATH}")
            except Exception as e:
                logger.error(f"Failed to parse {_CONFIG_PATH}: {e}. Using defaults.")
    else:
        logger.info(f"No {_CONFIG_PATH.name}; using built-in defaults.")

    # 2) apply env overrides
    for section, key, env in _ENV_MAP:
        raw = os.getenv(env)
        if raw is not None and raw != "":
            cfg[section][key] = _coerce(raw)

    # normalize a couple of fields
    cfg["stt"]["engine"] = str(cfg["stt"]["engine"]).strip().lower()
    return cfg


CONFIG = load_config()


def save_config(new_cfg: dict) -> dict:
    """Persist a full config dict to config.yaml and refresh the in-memory CONFIG.

    Returns the reloaded config. Only known sections/keys are kept, and values
    are coerced to the types of the current defaults where possible.
    """
    if yaml is None:
        raise RuntimeError("PyYAML not installed; cannot save config.")

    # Merge onto defaults so the file stays complete and well-formed.
    merged = _deep_merge({k: dict(v) for k, v in _DEFAULTS.items()}, new_cfg or {})
    # Keep only known sections/keys.
    clean = {}
    for section, keys in _DEFAULTS.items():
        clean[section] = {}
        for key, default in keys.items():
            val = merged.get(section, {}).get(key, default)
            # coerce to default's type when sensible
            if isinstance(default, bool):
                val = str(val).strip().lower() in ("true", "1", "yes") if not isinstance(val, bool) else val
            elif isinstance(default, int) and not isinstance(default, bool):
                try:
                    val = int(float(val))
                except (TypeError, ValueError):
                    val = default
            elif isinstance(default, float):
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    val = default
            clean[section][key] = val

    with open(_CONFIG_PATH, "w") as f:
        yaml.safe_dump(clean, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    logger.info(f"Saved config to {_CONFIG_PATH}")

    # refresh module-level CONFIG in place so other modules holding the ref see it
    global CONFIG
    CONFIG = load_config()
    return CONFIG
