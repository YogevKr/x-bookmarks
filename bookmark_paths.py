"""Runtime path resolution for local bookmark data."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ENV_VAR = "X_BOOKMARKS_HOME"
READ_ONLY_ENV = "X_BOOKMARKS_READ_ONLY"
CONFIG_ENV = "X_BOOKMARKS_CONFIG"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _env_value(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _config_candidates() -> list[Path]:
    override = _env_value(CONFIG_ENV)
    if override:
        return [Path(override).expanduser()]

    home = Path.home()
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.append(home / "Library" / "Application Support" / "x-bookmarks" / "config.json")

    xdg_home = _env_value("XDG_CONFIG_HOME")
    if xdg_home:
        candidates.append(Path(xdg_home).expanduser() / "x-bookmarks" / "config.json")
    else:
        candidates.append(home / ".config" / "x-bookmarks" / "config.json")

    candidates.append(home / ".x-bookmarks" / "config.json")
    candidates.append(home / ".x-bookmark" / "config.json")
    return candidates


def _load_config() -> tuple[dict, Path | None]:
    for path in _config_candidates():
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"x-bookmarks config must be a JSON object: {path}")
        return data, path
    return {}, None


def config_path() -> Path | None:
    path = _load_config()[1]
    return path.resolve() if path else None


def resolve_base_dir() -> Path:
    override = _env_value(ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    config, _ = _load_config()
    configured = str(config.get("base_dir", "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.cwd().resolve()


def read_only_mode() -> bool:
    if _env_value(READ_ONLY_ENV) is not None:
        return _env_flag(READ_ONLY_ENV)
    config, _ = _load_config()
    return bool(config.get("read_only"))
