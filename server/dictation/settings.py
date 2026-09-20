"""Configuration for the standalone dictation process."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Mapping

try:
    import yaml
except ImportError:  # Tests and --help work without optional macOS dependencies.
    yaml = None


@dataclass(frozen=True)
class DictationSettings:
    hotkey: str = "right_command"
    stt_backend: str = "parakeet"
    stt_model: str = "sonic-speech/parakeet-tdt-0.6b-v3-int8"
    language: str = "en"
    cleanup_enabled: bool = False
    cleanup_backend: str = "s1-mini"
    cleanup_model_path: str = ""
    sample_rate: int = 16000
    fail_closed_seconds: float = 1.5
    clipboard_restore_delay_ms: int = 150


_ENV = {field.name: f"DICTATION_{field.name.upper()}" for field in fields(DictationSettings)}


def _coerce(value: object, default: object) -> object:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
            raise ValueError(f"expected boolean, got {value!r}")
        return normalized in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return str(value)


def load_settings(
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> DictationSettings:
    """Load `dictation` YAML values, then apply namespaced environment overrides."""
    defaults = DictationSettings()
    values = {field.name: getattr(defaults, field.name) for field in fields(defaults)}
    config_path = path or Path(__file__).parents[1] / "config.yaml"

    if config_path.exists():
        if yaml is None:
            raise RuntimeError("PyYAML is required to read dictation configuration")
        raw = yaml.safe_load(config_path.read_text()) or {}
        section = raw.get("dictation", {})
        unknown = set(section) - set(values)
        if unknown:
            raise ValueError(f"unknown dictation setting(s): {', '.join(sorted(unknown))}")
        values.update(section)

    environment = os.environ if environ is None else environ
    for key, env_name in _ENV.items():
        if env_name in environment and environment[env_name] != "":
            values[key] = environment[env_name]

    coerced = {
        key: _coerce(value, getattr(defaults, key))
        for key, value in values.items()
    }
    settings = DictationSettings(**coerced)
    if settings.language.lower() != "en":
        raise ValueError("dictation MVP supports English only (language: en)")
    if not 0 < settings.fail_closed_seconds <= 1.5:
        raise ValueError("fail_closed_seconds must be greater than 0 and at most 1.5")
    if settings.cleanup_enabled and not settings.cleanup_model_path:
        raise ValueError("cleanup_model_path is required when cleanup_enabled is true")
    return settings

