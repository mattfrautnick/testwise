"""Configuration loading and validation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from testwise.exceptions import ConfigError
from testwise.models import TestwiseConfig


def get_repo_root() -> Path:
    """Get the root of the current git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise ConfigError(f"Not in a git repository: {e}") from e


def find_config_file(repo_root: Path) -> Path | None:
    """Look for .testwise.yml or .testwise.yaml in the repo root."""
    for name in (".testwise.yml", ".testwise.yaml"):
        path = repo_root / name
        if path.exists():
            return path
    return None


def load_config(
    config_path: Path | None = None,
    overrides: dict[str, object] | None = None,
) -> TestwiseConfig:
    """Load configuration from file and environment variables.

    Resolution order:
    1. Explicit config_path argument
    2. TESTWISE_CONFIG environment variable
    3. Auto-discover in repo root
    4. Defaults
    """
    raw: dict[str, object] = {}

    # Find config file
    if config_path is None:
        env_path = os.environ.get("TESTWISE_CONFIG")
        if env_path:
            config_path = Path(env_path)

    if config_path is None:
        try:
            repo_root = get_repo_root()
            config_path = find_config_file(repo_root)
        except ConfigError:
            pass

    # Load YAML
    if config_path is not None:
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")
        try:
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid YAML in {config_path}: {e}") from e

    # Apply environment variable overrides
    _apply_env_overrides(raw)

    # Apply explicit overrides
    if overrides:
        _deep_merge(raw, overrides)

    # Validate and return
    try:
        return TestwiseConfig.model_validate(raw)
    except Exception as e:
        raise ConfigError(f"Invalid configuration: {e}") from e


def _apply_env_overrides(raw: dict[str, object]) -> None:
    """Apply TESTWISE_* environment variables as config overrides."""
    env_map = {
        "TESTWISE_LLM_MODEL": ("llm", "model"),
        "TESTWISE_LLM_TEMPERATURE": ("llm", "temperature"),
        "TESTWISE_LLM_MAX_CONTEXT_TOKENS": ("llm", "max_context_tokens"),
        "TESTWISE_LLM_TIMEOUT": ("llm", "timeout_seconds"),
        "TESTWISE_FALLBACK_ON_ERROR": ("fallback_on_error",),
        "TESTWISE_RUN_SHOULD_RUN": ("run_should_run",),
    }

    for env_var, path in env_map.items():
        value = os.environ.get(env_var)
        if value is None:
            continue

        # Coerce types
        if path[-1] in ("temperature",):
            value = float(value)  # type: ignore[assignment]
        elif path[-1] in ("max_context_tokens", "timeout_seconds"):
            value = int(value)  # type: ignore[assignment]
        elif path[-1] in ("fallback_on_error", "run_should_run"):
            value = value.lower() in ("true", "1", "yes")  # type: ignore[assignment]

        # Set in raw dict
        target: dict[str, object] = raw
        for key in path[:-1]:
            nested = target.setdefault(key, {})
            assert isinstance(nested, dict)
            target = nested
        target[path[-1]] = value

    # API key env var name override
    api_key_env = os.environ.get("TESTWISE_API_KEY_ENV")
    if api_key_env:
        llm = raw.setdefault("llm", {})
        assert isinstance(llm, dict)
        llm["api_key_env"] = api_key_env


def _deep_merge(base: dict[str, object], override: dict[str, object]) -> None:
    """Merge override into base, modifying base in place."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)  # type: ignore[arg-type]
        else:
            base[key] = value
