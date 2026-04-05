"""Runtime path resolution for local bookmark data."""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "X_BOOKMARKS_HOME"
READ_ONLY_ENV = "X_BOOKMARKS_READ_ONLY"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def resolve_base_dir() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return Path.cwd().resolve()


def read_only_mode() -> bool:
    return _env_flag(READ_ONLY_ENV)
